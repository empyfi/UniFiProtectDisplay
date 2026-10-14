# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one object that owns the plugin's state for a session.

Everything with a lifetime longer than a single screen lives here: the API
client, the worker pool, the camera store, the rotation cursor, and whichever
corner mode is currently running. The setup screen and the extensions menu
both talk to this, so a settings change takes effect in one place rather than
being re-derived in three.

A single instance per enigma2 session, created at session start.
"""

from enigma import eTimer

from . import settings as settings_module
from .async_util import TaskRunner
from .camerastore import CameraStore
from .capabilities import Capabilities
from .controller import Controller
from .logger import Logger
from .protect.api import ProtectApi
from .rotation import Rotation
from .settings import MODE_OFF, MODE_PIP, MODE_SNAPSHOT
from .localisation import _


class DisplayManager(object):
	"""Owns the plugin's long-lived state for one enigma2 session."""

	instance = None

	def __init__(self, session):
		self.session = session
		config = settings_module.get_config()
		self.log = Logger(debug=config.debug.value)
		self.capabilities = Capabilities()
		self.log.info("starting: %s", self.capabilities.summary())

		self.runner = TaskRunner(timer_factory=eTimer, log=self.log)
		self.store = CameraStore(log=self.log).load()
		self.rotation = Rotation(
			self.store.enabled_ids(), enabled=config.rotate.value)
		self.api = self._build_api(config)
		self.controller = Controller(
			self.api, self.runner, self.store,
			capabilities=self.capabilities,
			settings=settings_module.build_settings(config),
			log=self.log)

		self._overlay = None
		self._pip = None

	@classmethod
	def get(cls, session=None):
		"""The session's manager, created on first use."""
		if cls.instance is None and session is not None:
			cls.instance = cls(session)
		return cls.instance

	# -- configuration ----------------------------------------------------

	def _build_api(self, config):
		return ProtectApi(
			host=config.host.value,
			api_key=config.apikey.value,
			verify_ssl=config.verify_ssl.value,
			log=self.log)

	def reload_settings(self):
		"""Re-read the config after the setup screen closes.

		Rebuilds the API client and the settings snapshot, and restarts the
		corner if it was running, so geometry and interval changes apply
		immediately rather than at the next reboot.
		"""
		config = settings_module.get_config()
		self.log.debug_enabled = config.debug.value
		self.api = self._build_api(config)
		self.controller.api = self.api
		self.controller.settings = settings_module.build_settings(config)
		# A changed quality or transport makes every cached URL wrong.
		self.controller.invalidate_stream()
		self.rotation.enabled = config.rotate.value
		self.rotation.set_cameras(self.store.enabled_ids())

		# Restart only what was already on screen. Saving settings is not a
		# request to switch the corner on, and a corner appearing by itself
		# because the user adjusted an unrelated option would be surprising.
		was_running = self.corner_running()
		self.stop_corner()
		if was_running and config.corner_mode.value != MODE_OFF:
			self.start_corner()

	def refresh_selection(self):
		"""Pick up a change made in the camera list screen."""
		self.rotation.set_cameras(self.store.enabled_ids())

	# -- the corner -------------------------------------------------------

	def corner_running(self):
		return self._overlay is not None or (
			self._pip is not None and self._pip.active)

	def start_corner(self):
		"""Start whichever corner mode is configured. Returns ``(ok, message)``."""
		config = settings_module.get_config()
		mode = config.corner_mode.value
		if mode == MODE_OFF:
			return False, _("The corner display is switched off in setup.")
		if self.corner_running():
			return True, ""
		if not self.rotation.camera_ids():
			return False, _(
				"No cameras are selected. Open the camera list first.")
		if mode == MODE_SNAPSHOT:
			return self._start_snapshot(config)
		if mode == MODE_PIP:
			return self._start_pip(config)
		return False, _("Unknown display mode: %s") % mode

	def _start_snapshot(self, config):
		from .render.snapshot_overlay import SnapshotOverlay
		try:
			self._overlay = self.session.instantiateDialog(
				SnapshotOverlay,
				self.controller, self.rotation,
				corner=config.corner.value,
				width_percent=config.corner_width.value,
				margin_percent=config.corner_margin.value,
				interval=config.snapshot_interval.value,
				rotate_interval=config.rotate_interval.value,
				follow_pip=config.follow_pip.value,
				log=self.log)
			self._overlay.show()
		except Exception as exc:  # noqa: BLE001
			self.log.error("could not start the snapshot overlay: %s", exc)
			self._overlay = None
			return False, _("Could not start the overlay: %s") % exc
		return True, ""

	def _start_pip(self, config):
		from .player.pip_remux import PipCorner
		self._pip = PipCorner(
			self.session, self.controller, self.rotation, self.capabilities,
			corner=config.corner.value,
			width_percent=config.corner_width.value,
			margin_percent=config.corner_margin.value,
			rotate_interval=config.rotate_interval.value,
			proxy_port=config.proxy_port.value,
			follow_pip=config.follow_pip.value,
			log=self.log)
		ok, message = self._pip.start()
		if not ok:
			self._pip = None
		return ok, message

	def stop_corner(self):
		if self._overlay is not None:
			try:
				# deleteDialog hides and closes, which runs the overlay's
				# own onClose cleanup (timers, pending fetch, spool files).
				self.session.deleteDialog(self._overlay)
			except Exception as exc:  # noqa: BLE001
				self.log.error("while closing the overlay: %s", exc)
			self._overlay = None
		if self._pip is not None:
			self._pip.stop()
			self._pip = None

	def toggle_corner(self):
		if self.corner_running():
			self.stop_corner()
			return True, ""
		return self.start_corner()

	# -- fullscreen -------------------------------------------------------

	def open_fullscreen(self):
		"""Open the fullscreen viewer.

		The corner is stopped first: in snapshot mode it would sit on top of
		the fullscreen picture, and in PiP mode both would compete for the
		camera and the decoders.
		"""
		if not self.rotation.camera_ids():
			return False, _(
				"No cameras are selected. Open the camera list first.")
		self.stop_corner()
		from .player.fullscreen import FullscreenViewer
		config = settings_module.get_config()
		self.session.open(
			FullscreenViewer, self.controller, self.rotation,
			rotate_interval=config.rotate_interval.value, log=self.log,
			capabilities=self.capabilities,
			proxy_port=config.proxy_port.value)
		return True, ""

	# -- misc -------------------------------------------------------------

	def next_camera(self):
		if self._overlay is not None:
			self._overlay.next_camera()
		elif self._pip is not None and self._pip.active:
			self._pip.next_camera()
		else:
			self.rotation.next()

	def shutdown(self):
		"""Release everything. Called when the session ends."""
		self.stop_corner()
		self.runner.shutdown()
		DisplayManager.instance = None
