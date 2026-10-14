# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live camera video in a corner, over live television.

How it works, and why it has to
-------------------------------
PiP is the only way to get a second picture on screen, and PiP will only
accept a service whose ``setTarget(1)`` succeeds. That rules out every
software player: ``eServiceMP3`` (4097) and ``eServiceApp`` (5001/5002) both
``return -1``. What is left is ``eDVBServicePlay`` (type 1), which can read a
stream -- but through ``eHttpStream``, which speaks HTTP and expects MPEG-TS.

So the chain is:

	camera --RTSP--> ffmpeg -c copy --MPEG-TS--> loopback HTTP
	       --> type 1 service --> PiP decoder --> corner of the screen

``-c copy`` means repackaging, not re-encoding, which is the only reason this
is affordable on a set-top box.

Interacting with enigma2's own PiP
----------------------------------
``session.pip`` and ``session.pipshown`` belong to ``InfoBarPiP``. This module
borrows them using exactly the sequence ``InfoBarGenerics.showPiP`` uses, so
enigma2's own state stays consistent and its PiP button still works -- it
simply closes the camera. Because enigma2 can tear the PiP down without
telling us, a watchdog notices and runs our cleanup.

``config.av.pip`` is the user's own PiP geometry. We overwrite it to place the
corner, so the previous values are saved and put back on stop.
"""

from enigma import ePoint, eServiceReference, eSize, eTimer

from Components.config import config
from Screens.PictureInPicture import PictureInPicture

from .. import geometry, servicerefs
from .tsproxy import TsProxy
from ..localisation import _, blocker_text

# How often to check that enigma2 has not closed our PiP behind our back.
WATCHDOG_INTERVAL_MS = 2000


class PipCorner(object):
	"""Drives a camera into the PiP window and keeps it there.

	Not a ``Screen``: PiP is a session-level object, and the corner has no
	UI of its own beyond the picture.
	"""

	def __init__(self, session, controller, rotation, capabilities,
			corner="topright", width_percent=25, margin_percent=2,
			rotate_interval=15, proxy_port=0, log=None, proxy=None,
			follow_pip=True, aspect=geometry.DEFAULT_ASPECT,
			fit=geometry.FIT_FILL, on_camera_shown=None):
		self.session = session
		self._controller = controller
		self._rotation = rotation
		self._capabilities = capabilities
		self._corner = corner
		self._width_percent = width_percent
		self._margin_percent = margin_percent
		self._rotate_interval = max(1, int(rotate_interval))
		self._follow_pip = follow_pip
		self._aspect = aspect
		self._fit = fit
		# Told whenever a camera reaches the screen, so the manager can
		# re-check which cameras are reachable.
		self._on_camera_shown = on_camera_shown
		self._log = log
		self._proxy = proxy or TsProxy(
			ffmpeg_path=(capabilities.which("ffmpeg") or "ffmpeg")
			if capabilities else "ffmpeg",
			port=proxy_port, log=log)
		self._saved_mode = None
		self._pending = None
		self._active = False
		self._rotate_timer = None
		self._watchdog = None

	@property
	def active(self):
		return self._active

	# -- starting ---------------------------------------------------------

	def start(self):
		"""Bring the corner up. Returns ``(ok, message)``.

		The message is shown to the user when ``ok`` is false; every refusal
		names something they can act on.
		"""
		if self._active:
			return True, ""

		if self._capabilities is not None:
			blocker = blocker_text(self._capabilities.remux_pip_blockers())
			if blocker:
				return False, _("Live video in a corner is not possible: %s") % blocker

		if self._pip_in_use():
			# Taking over the user's own PiP would silently close whatever
			# they were watching in it.
			return False, _(
				"Picture in Picture is already in use. Close it first.")

		try:
			self._proxy.start()
		except Exception as exc:  # noqa: BLE001
			self._error("could not start the TS proxy: %s", exc)
			return False, _("Could not start the local stream proxy: %s") % exc

		try:
			self._create_pip()
		except Exception as exc:  # noqa: BLE001
			self._error("could not create the PiP window: %s", exc)
			self._release_standard_mode()
			self._proxy.stop()
			return False, _("Could not open the PiP window: %s") % exc

		self._active = True
		self._start_timers()
		self._play_current()
		return True, ""

	def _pip_in_use(self):
		# Mirrors the guard in InfoBarGenerics: both halves are checked
		# because the two can briefly disagree during teardown.
		return (hasattr(self.session, "pip")
			or getattr(self.session, "pipshown", False))

	def _create_pip(self):
		# config.av.pip is the user's own Picture-in-Picture geometry. It is
		# deliberately never written here: PictureInPicture.move() and
		# .resize() persist straight into it, so using them would replace
		# whatever position and size the user had set for their own PiP, and
		# a plugin borrowing the window has no business doing that. The
		# window is positioned through its widgets instead, which changes
		# what is on screen and nothing on disk.
		self._borrow_standard_mode()
		self.session.pip = self.session.instantiateDialog(PictureInPicture)
		self.session.pip.setAnimationMode(0)
		self.session.pip.show()
		self._apply_geometry()
		# Keep InfoBar's own state truthful, so its PiP button and its
		# guards behave. Pressing it will close our corner, which is the
		# behaviour a user would expect from that button anyway.
		self.session.pipshown = True
		# InfoBar's swapPiP reads this; give it the user's current path
		# rather than leaving the attribute missing.
		try:
			self.session.pip.servicePath = None
		except AttributeError:
			pass

	# -- playback ---------------------------------------------------------

	def _play_current(self):
		camera_id = self._rotation.current()
		if camera_id is None:
			return
		if self._on_camera_shown is not None:
			self._on_camera_shown()
		if self._pending is not None:
			self._pending.cancel()
		name = self._controller.store.name_for(camera_id)
		self._pending = self._controller.stream_url(
			camera_id,
			on_done=lambda url: self._play_url(url, camera_id, name),
			on_error=self._failed)

	def _play_url(self, rtsp_url, camera_id, name):
		self._pending = None
		if not self._active:
			return
		try:
			local_url = self._proxy.register(camera_id, rtsp_url)
		except RuntimeError as exc:
			self._error("proxy refused the camera: %s", exc)
			return
		reference = servicerefs.build_pip_reference(local_url, name)
		self._info("PiP playing camera %s", camera_id)
		played = self.session.pip.playService(eServiceReference(reference))
		if not played:
			# enigma2 has already shown its own popup; add the context it
			# cannot know, because this is the single most informative
			# failure in the whole plugin.
			self._error(
				"PiP refused the service. The box reported no free decoder, "
				"or it cannot decode this stream. Try a lower camera "
				"quality (H.264 rather than H.265).")

	def _failed(self, exc):
		self._pending = None
		self._error("%s", self._controller.describe_error(exc))
		if self._rotation.should_rotate():
			self._rotation.advance()

	# -- rotation and supervision -----------------------------------------

	def _start_timers(self):
		if self._rotation.should_rotate():
			self._rotate_timer = eTimer()
			self._rotate_timer.callback.append(self._rotate)
			self._rotate_timer.start(self._rotate_interval * 1000, False)
		self._watchdog = eTimer()
		self._watchdog.callback.append(self._check_still_there)
		self._watchdog.start(WATCHDOG_INTERVAL_MS, False)

	def _rotate(self):
		if self._rotation.should_rotate():
			self._rotation.advance()
			self._play_current()

	def _check_still_there(self):
		"""Clean up if enigma2 closed the PiP without going through us.

		The PiP button, a zap to a service that needs the decoder, or a
		standby cycle can all remove ``session.pip``. Without this the
		geometry would stay overwritten and ffmpeg would keep running.
		"""
		if self._active and not hasattr(self.session, "pip"):
			self._info("PiP was closed elsewhere; cleaning up")
			self.stop(pip_already_gone=True)

	def next_camera(self):
		self._rotation.next()
		if self._rotate_timer is not None:
			self._rotate_timer.stop()
			self._rotate_timer.start(self._rotate_interval * 1000, False)
		self._play_current()

	# -- stopping ---------------------------------------------------------

	def stop(self, pip_already_gone=False):
		"""Tear everything down and give the user their PiP settings back."""
		if not self._active:
			return
		self._active = False

		for timer in (self._rotate_timer, self._watchdog):
			if timer is not None:
				timer.stop()
		self._rotate_timer = None
		self._watchdog = None

		if self._pending is not None:
			self._pending.cancel()
			self._pending = None

		if not pip_already_gone and hasattr(self.session, "pip"):
			try:
				del self.session.pip
			except Exception as exc:  # noqa: BLE001
				self._error("while closing PiP: %s", exc)
		self.session.pipshown = False

		# Stopping the proxy kills ffmpeg: no camera traffic survives the
		# corner being switched off.
		self._proxy.stop()
		self._release_standard_mode()
		self._info("PiP corner stopped")

	# -- the user's PiP geometry ------------------------------------------

	def reapply_geometry(self):
		"""Re-assert the geometry, e.g. after the user edited the window."""
		if self._active and hasattr(self.session, "pip"):
			self._apply_geometry()

	def _apply_geometry(self):
		"""Size the window exactly like the box's own Picture-in-Picture.

		The camera is shown in the window the user already arranged for PiP,
		at that position and that size, because having two differently
		placed small windows for the same hardware decoder is confusing and
		means setting the same thing twice.

		The video inside is letterboxed rather than stretched: a 16:9 camera
		in enigma2's 4:3-ish default window would otherwise be visibly
		squashed. Bars cost a little picture; distortion costs more.

		Still not via ``PictureInPicture.move()`` / ``.resize()`` -- those
		persist into ``config.av.pip``, and reading the user's geometry must
		not end up rewriting it.
		"""
		if self._follow_pip:
			rect = geometry.pip_config_rect(self._current_pip_values())
		else:
			rect = geometry.pip_rect(
				self._corner, self._width_percent, self._margin_percent)
		x, y, width, height = rect

		pip = self.session.pip
		try:
			pip.instance.move(ePoint(x, y))
			pip.instance.resize(eSize(width, height))
			# The video widget is positioned inside the window, so the bars
			# are simply the part of the window it does not cover.
			vx, vy, vwidth, vheight = geometry.fit_rect(
				width, height, self._aspect, self._fit)
			video = pip["video"].instance
			video.move(ePoint(vx, vy))
			video.resize(eSize(vwidth, vheight))
		except (AttributeError, RuntimeError, KeyError) as exc:
			self._error("could not place the PiP window: %s", exc)
			return
		self._info("PiP corner at %d,%d %dx%d, video %dx%d at +%d+%d",
			x, y, width, height, vwidth, vheight, vx, vy)

	def _current_pip_values(self):
		"""config.av.pip as a plain list, or None when it does not exist."""
		try:
			return list(config.av.pip.value)
		except (AttributeError, TypeError):
			# Created by PictureInPicture itself; absent on first ever use,
			# in which case the enigma2 default is the right answer.
			return None

	def _borrow_standard_mode(self):
		"""Switch PiP to "standard" placement in memory only.

		Every other mode forces its own fixed layout, and the cascade and
		split modes also shrink the main picture. The value is changed
		without ``save()``, so ``saved_value`` -- what actually reaches
		/etc/enigma2/settings -- still holds the user's choice, and
		:meth:`_release_standard_mode` puts the live value back.
		"""
		self._saved_mode = None
		try:
			if config.av.pip_mode.value != "standard":
				self._saved_mode = config.av.pip_mode.value
				config.av.pip_mode.value = "standard"
		except AttributeError:
			# config.av.pip_mode is created by PictureInPicture itself and
			# may not exist on the very first use; the default is
			# "standard", which is what we want anyway.
			pass

	def _release_standard_mode(self):
		if self._saved_mode is None:
			return
		try:
			config.av.pip_mode.value = self._saved_mode
		except AttributeError:
			pass
		finally:
			self._saved_mode = None

	# -- logging ----------------------------------------------------------

	def _info(self, message, *args):
		if self._log is not None:
			self._log.info(message, *args)

	def _error(self, message, *args):
		if self._log is not None:
			self._log.error(message, *args)
