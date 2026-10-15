# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""The snapshot corner: camera stills painted over live television.

Why stills and not video
------------------------
Real video in a corner needs a second hardware decoder, and enigma2 will only
put a service there if it implements ``setTarget(1)``. Every software player
returns -1 from it (``eServiceMP3`` and ``eServiceApp`` alike), so an RTSP
camera can never occupy the PiP decoder directly. Stills sidestep decoders
entirely: a JPEG is fetched, decoded by ``ePicLoad`` and blitted into an
``ePixmap``, which composites over whatever the decoder happens to be showing.

The result is one to two frames a second rather than live video, and it works
on every box, alongside live TV, with no extra packages. For real motion, see
:mod:`player.pip_remux`.

Decode path
-----------
``ePicLoad`` reads from a file, not from memory, so each snapshot is written
to tmpfs first. Two filenames alternate: a decode runs on its own thread, and
overwriting the file it is reading would hand it half an image.
"""

import os

from enigma import ePicLoad, eTimer

from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen

from .. import geometry
from ..localisation import _

# tmpfs on every enigma2 image, so this never touches the flash. A receiver
# that writes a JPEG to NAND twice a second would wear it out.
SPOOL_DIR = "/tmp"
SPOOL_NAMES = ("upd_snapshot_a.jpg", "upd_snapshot_b.jpg")

# Background behind the picture while the first frame is still on its way.
BACKDROP = "#40000000"

# After this many consecutive failures the overlay says so on screen instead
# of leaving a stale picture that looks live.
FAILURES_BEFORE_COMPLAINT = 3


class SnapshotOverlay(Screen):
	"""Non-interactive overlay showing one camera at a time.

	Instantiated with ``session.instantiateDialog`` and shown; it takes no
	key focus, so the user keeps full control of the receiver while it runs.
	"""

	def __init__(self, session, controller, rotation, corner="topright",
			width_percent=25, margin_percent=2, interval=2, rotate_interval=15,
			follow_pip=True, on_camera_shown=None, log=None):
		self._controller = controller
		self._rotation = rotation
		self._log = log
		self._interval = max(1, int(interval))
		self._rotate_interval = max(1, int(rotate_interval))
		self._elapsed = 0
		self._failures = 0
		self._spool_index = 0
		self._pending = None
		self._decoding = False
		self._showing_error = False
		self._on_camera_shown = on_camera_shown

		desktop_size = _desktop_size(session)
		if follow_pip:
			# Match the box's own PiP window, so both corner modes appear in
			# the same place and only one geometry has to be set up. That
			# geometry lives in PiP's fixed 720x576 grid and has to be
			# scaled into skin space here.
			rect = geometry.to_skin_space(
				geometry.pip_config_rect(_pip_values()),
				desktop_size[0], desktop_size[1])
		else:
			rect = geometry.skin_rect(
				corner, desktop_size[0], desktop_size[1], width_percent,
				margin_percent)
		self._rect = rect
		self.skin = _build_skin(rect)
		Screen.__init__(self, session)

		self["picture"] = Pixmap()
		self["label"] = Label("")

		self._picload = ePicLoad()
		# PSignal: the C++ side calls back on the main loop once the decode
		# thread has finished, so touching widgets from here is safe.
		self._picload.PictureData.get().append(self._picture_decoded)

		self._timer = eTimer()
		self._timer.callback.append(self._tick)

		self.onLayoutFinish.append(self._started)
		self.onClose.append(self._stopped)

	# -- lifecycle --------------------------------------------------------

	def _started(self):
		width, height = self._rect[2], self._rect[3]
		# (aspectRatio, as) = (1, 1) keeps the source proportions: ePicLoad
		# computes aspect_ratio as aspectRatio/as and only stretches when it
		# is 0. The corner rectangle is 16:9, which matches most cameras, but
		# a 4:3 or package-camera lens must letterbox inside it rather than
		# be distorted. resizetype 1 picks the interpolating resampler, worth
		# its cost on a picture this small.
		self._picload.setPara((width, height, 1, 1, False, 1, BACKDROP))
		# Name the camera straight away, so the corner is identifiable during
		# the second or two before the first picture arrives.
		self._update_label()
		self._timer.start(self._interval * 1000, False)
		self._request()

	def _stopped(self):
		self._timer.stop()
		if self._pending is not None:
			# A snapshot in flight must not be painted into a widget that is
			# being torn down.
			self._pending.cancel()
			self._pending = None
		try:
			self._picload.PictureData.get().remove(self._picture_decoded)
		except (ValueError, AttributeError):
			pass
		self._picload = None
		_remove_spool_files()

	# -- polling ----------------------------------------------------------

	def _tick(self):
		self._elapsed += self._interval
		if self._elapsed >= self._rotate_interval:
			self._elapsed = 0
			if self._rotation.should_rotate():
				self._rotation.advance()
				self._update_label()
				if self._on_camera_shown is not None:
					self._on_camera_shown()
		self._request()

	def _request(self):
		camera_id = self._rotation.current()
		if camera_id is None:
			self._show_message(_("No camera selected"))
			return
		if not self._controller.store.is_connected(camera_id):
			# Asking the console for a snapshot it cannot take wastes a
			# request per tick and leaves the last camera's picture up.
			self._show_offline(camera_id)
			return
		if self._pending is not None:
			# The previous fetch has not come back. Skipping rather than
			# queueing keeps a slow console from building a backlog that
			# would then flood the decoder.
			return
		self._pending = self._controller.snapshot(
			camera_id,
			on_done=self._snapshot_received,
			on_error=self._snapshot_failed)

	def _snapshot_received(self, data):
		self._pending = None
		self._failures = 0
		path = self._write_spool(data)
		if path is None:
			return
		if self._picload is None:
			return
		self._decoding = True
		# Asynchronous: the decode runs on ePicLoad's own thread and comes
		# back through PictureData, so the main loop keeps running.
		self._picload.startDecode(path, 0, 0, True)

	def _snapshot_failed(self, exc):
		self._pending = None
		self._failures += 1
		if self._failures >= FAILURES_BEFORE_COMPLAINT:
			# Say it plainly: a frozen last frame looks like a working
			# camera, which is the worst possible failure mode here.
			self._show_message(self._controller.describe_error(exc))

	# -- decoding ---------------------------------------------------------

	def _picture_decoded(self, info):
		self._decoding = False
		if self._picload is None:
			return
		pixmap = self["picture"].instance
		if pixmap is None:
			return
		try:
			data = self._picload.getData()
		except Exception as exc:  # noqa: BLE001
			self._warn("could not read the decoded picture: %s", exc)
			return
		if data is None:
			return
		# getData() hands back an ePtr<gPixmap>; setPixmap wants the pointed-to
		# object. Enigma2's own picture viewer dereferences it the same way.
		# Some SWIG builds accept the ePtr directly, hence the fallback.
		try:
			pixmap.setPixmap(data.__deref__())
		except AttributeError:
			pixmap.setPixmap(data)
		self._clear_message()

	def _write_spool(self, data):
		"""Write the JPEG to tmpfs and return its path.

		Alternates between two names so the file a decode is still reading
		is never the one being overwritten.
		"""
		self._spool_index = (self._spool_index + 1) % len(SPOOL_NAMES)
		path = os.path.join(SPOOL_DIR, SPOOL_NAMES[self._spool_index])
		try:
			with open(path, "wb") as handle:
				handle.write(data)
			return path
		except (OSError, IOError) as exc:
			self._warn("could not write the snapshot: %s", exc)
			return None

	# -- on-screen text ---------------------------------------------------

	def _update_label(self):
		if self._showing_error:
			return
		camera_id = self._rotation.current()
		if camera_id is None:
			return
		self["label"].setText(self._controller.store.name_for(camera_id))

	def _show_offline(self, camera_id):
		"""Blank the picture and label the camera as unreachable."""
		pixmap = self["picture"].instance
		if pixmap is not None:
			try:
				pixmap.setPixmap(None)
			except Exception:  # noqa: BLE001
				pass
		self._show_message("%s - %s" % (
			self._controller.store.name_for(camera_id), _("offline")))

	def _show_message(self, text):
		self._showing_error = True
		self["label"].setText(text)

	def _clear_message(self):
		if self._showing_error:
			self._showing_error = False
		self._update_label()

	# -- control from outside ---------------------------------------------

	def next_camera(self):
		"""Manual skip, from the extensions menu."""
		self._rotation.next()
		self._elapsed = 0
		self._update_label()
		self._request()

	def set_interval(self, seconds):
		self._interval = max(1, int(seconds))
		self._timer.stop()
		self._timer.start(self._interval * 1000, False)

	def _warn(self, message, *args):
		if self._log is not None:
			self._log.error(message, *args)


def _build_skin(rect):
	"""Skin XML for the overlay, sized to the computed corner rectangle.

	Built here rather than shipped as a file because position and size are
	user settings, and a skin file cannot read the config.
	"""
	x, y, width, height = rect
	label_height = max(18, min(28, height // 6))
	return """
		<screen name="UnifiProtectSnapshotOverlay" position="%d,%d" size="%d,%d"
				flags="wfNoBorder" backgroundColor="%s" zPosition="10" title=" ">
			<widget name="picture" position="0,0" size="%d,%d"
					zPosition="1" alphatest="off" />
			<widget name="label" position="0,%d" size="%d,%d"
					font="Regular;%d" halign="center" valign="center"
					zPosition="2" transparent="0"
					foregroundColor="#ffffff" backgroundColor="#80000000" />
		</screen>""" % (
		x, y, width, height, BACKDROP,
		width, height,
		height - label_height, width, label_height,
		max(14, label_height - 6))


def _pip_values():
	"""config.av.pip as a plain list, or None when enigma2 has not made it."""
	try:
		from Components.config import config
		return list(config.av.pip.value)
	except (AttributeError, TypeError, ImportError):
		return None


def _desktop_size(session):
	"""Skin resolution, with a safe fallback.

	The overlay lives in skin space, which is whatever the active skin
	declares -- commonly 1280x720 or 1920x1080, and emphatically not PiP's
	fixed 720x576 grid.
	"""
	try:
		from enigma import getDesktop
		size = getDesktop(0).size()
		width, height = size.width(), size.height()
		if width > 0 and height > 0:
			return width, height
	except Exception:  # noqa: BLE001
		pass
	return 1280, 720


def _remove_spool_files():
	for name in SPOOL_NAMES:
		try:
			os.remove(os.path.join(SPOOL_DIR, name))
		except (OSError, IOError):
			pass
