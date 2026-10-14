# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fullscreen camera playback on the main decoder.

This is the straightforward half of the plugin. The main decoder has no
``setTarget`` constraint, so a camera can be played with an ordinary software
player: GStreamer (4097) on any image, or exteplayer3 (5002) where serviceapp
is installed. The cost is that live television stops while a camera is up,
which is why the service playing beforehand is restored on exit.
"""

from enigma import eServiceReference, eTimer

from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen

from .. import servicerefs
from .tsproxy import TsProxy
from ..localisation import _


class FullscreenViewer(Screen):
	"""Plays one camera at a time, full screen, with rotation.

	The screen itself is transparent apart from a caption: the picture comes
	from the video decoder behind it, not from a widget.
	"""

	skin = """
		<!-- Captions use transparent="0" with a half-transparent dark
		     background: white text alone is unreadable over a bright
		     camera picture. In enigma2 a colour is #TTRRGGBB, where TT is
		     transparency, so #ff000000 is fully see-through. -->
		<screen name="UnifiProtectFullscreen" position="0,0" size="e,e"
				flags="wfNoBorder" backgroundColor="#ff000000" title=" ">
			<widget name="caption" position="40,40" size="e-80,48"
					font="Regular;34" halign="left" valign="center"
					transparent="0" foregroundColor="#ffffff"
					backgroundColor="#80000000" zPosition="2" />
			<widget name="status" position="40,e-90" size="e-80,50"
					font="Regular;24" halign="left" valign="center"
					transparent="0" foregroundColor="#ffcc66"
					backgroundColor="#80000000" zPosition="2" />
		</screen>"""

	def __init__(self, session, controller, rotation, rotate_interval=15,
			log=None, capabilities=None, proxy_port=0):
		Screen.__init__(self, session)
		self._controller = controller
		self._rotation = rotation
		self._log = log
		self._capabilities = capabilities
		self._proxy_port = proxy_port
		# Protect serves rtsps:// with ?enableSrtp. GStreamer's rtspsrc does
		# not do SRTP, so handing it that URL yields a black screen with no
		# error worth the name. ffmpeg handles it, so when ffmpeg is present
		# the stream is remuxed through the same loopback proxy the PiP
		# corner uses and played as plain HTTP MPEG-TS, which every player
		# on these images reads. Without ffmpeg we still try the direct URL:
		# it works for plain rtsp:// and costs nothing to attempt.
		self._proxy = None
		self._rotate_interval = max(1, int(rotate_interval))
		self._previous_service = None
		self._pending = None
		self._closing = False

		self["caption"] = Label("")
		self["status"] = Label("")

		self["actions"] = ActionMap(
			["OkCancelActions", "DirectionActions", "ColorActions"], {
				"cancel": self.close,
				"ok": self.next_camera,
				"right": self.next_camera,
				"left": self.previous_camera,
				# Pausing matters when the user is actually watching one
				# camera: rotation sliding away mid-look is maddening.
				"yellow": self.toggle_pause,
			}, -1)

		self._timer = eTimer()
		self._timer.callback.append(self._rotate)

		self.onLayoutFinish.append(self._started)
		self.onClose.append(self._stopped)

	# -- lifecycle --------------------------------------------------------

	def _started(self):
		# Remember what the user was watching so EXIT puts it back. Without
		# this, leaving the plugin would drop them on a black screen.
		self._previous_service = \
			self.session.nav.getCurrentlyPlayingServiceReference()
		self._play_current()
		if self._rotation.should_rotate():
			self._timer.start(self._rotate_interval * 1000, False)

	def _stopped(self):
		self._closing = True
		self._timer.stop()
		if self._proxy is not None:
			# Stopping the proxy kills ffmpeg, so no camera traffic survives
			# the viewer being closed.
			self._proxy.stop()
			self._proxy = None
		if self._pending is not None:
			self._pending.cancel()
			self._pending = None
		self._restore_service()

	def _restore_service(self):
		if self._previous_service is not None:
			self.session.nav.playService(self._previous_service)
		else:
			# Nothing was playing (the user came from standby or a menu);
			# stopping is better than leaving the camera running unseen.
			self.session.nav.stopService()

	# -- playback ---------------------------------------------------------

	def _play_current(self):
		camera_id = self._rotation.current()
		if camera_id is None:
			self["status"].setText(_("No camera selected"))
			return
		name = self._controller.store.name_for(camera_id)
		self["caption"].setText(name)
		self["status"].setText(_("Connecting..."))
		if self._pending is not None:
			self._pending.cancel()
		self._pending = self._controller.stream_url(
			camera_id,
			on_done=lambda url: self._play_url(url, camera_id, name),
			on_error=self._failed)

	def _play_url(self, rtsp_url, camera_id, name):
		self._pending = None
		if self._closing:
			return
		url, via = self._playable_url(rtsp_url, camera_id)
		if url is None:
			self["status"].setText(
				_("Could not prepare the stream for playback."))
			return
		player = self._controller.player_name()
		reference = servicerefs.build_stream_reference(url, name, player)
		self._info("playing camera %s with %s (%s)", camera_id, player, via)
		self.session.nav.playService(eServiceReference(reference))
		self["status"].setText("")

	def _playable_url(self, rtsp_url, camera_id):
		"""Return a URL this box can actually play, and how it was obtained."""
		has_ffmpeg = self._capabilities is None or self._capabilities.has_ffmpeg()
		if not has_ffmpeg:
			return rtsp_url, "direct RTSP, no ffmpeg available"
		try:
			if self._proxy is None:
				ffmpeg = (self._capabilities.which("ffmpeg")
					if self._capabilities else None) or "ffmpeg"
				self._proxy = TsProxy(
					ffmpeg_path=ffmpeg, port=self._proxy_port, log=self._log)
				self._proxy.start()
			return self._proxy.register(camera_id, rtsp_url), "ffmpeg remux"
		except Exception as exc:  # noqa: BLE001
			# Falling back beats failing: a direct URL may still work.
			self._warn("could not start the remux proxy (%s); "
				"falling back to the direct URL", exc)
			return rtsp_url, "direct RTSP after proxy failure"

	def _failed(self, exc):
		self._pending = None
		if self._closing:
			return
		message = self._controller.describe_error(exc)
		self["status"].setText(message)
		# A camera that cannot be resolved should not stall the rotation on
		# everyone else's behalf.
		if self._rotation.should_rotate():
			self._rotation.advance()

	# -- navigation -------------------------------------------------------

	def _rotate(self):
		if self._rotation.should_rotate():
			self._rotation.advance()
			self._play_current()

	def next_camera(self):
		self._rotation.next()
		self._restart_timer()
		self._play_current()

	def previous_camera(self):
		self._rotation.previous()
		self._restart_timer()
		self._play_current()

	def toggle_pause(self):
		if self._rotation.paused:
			self._rotation.resume()
			self["status"].setText("")
			self._restart_timer()
		else:
			self._rotation.pause()
			self._timer.stop()
			self["status"].setText(_("Rotation paused"))

	def _restart_timer(self):
		self._timer.stop()
		if self._rotation.should_rotate():
			self._timer.start(self._rotate_interval * 1000, False)

	def _info(self, message, *args):
		if self._log is not None:
			self._log.info(message, *args)

	def _warn(self, message, *args):
		if self._log is not None:
			self._log.error(message, *args)
