# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""The setup screen.

Two things here are more than plumbing. The list is rebuilt as the user moves
through it, so options that do not apply are not shown at all -- there is no
point offering a snapshot interval when the corner is set to live video. And
the live-video mode is shown with the reason it is unavailable rather than
being silently missing, because "ffmpeg is not installed" is something the
user can act on.
"""

from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.Sources.StaticText import StaticText
from Screens.Screen import Screen

from .. import settings as settings_module
from ..settings import MODE_OFF, MODE_PIP, MODE_SNAPSHOT
from ..localisation import _, blocker_text


class UnifiProtectSetup(ConfigListScreen, Screen):
	skin = """
		<screen name="UnifiProtectSetup" position="center,center" size="1000,600"
				title="UniFi Protect Display">
			<widget name="config" position="10,10" size="980,430"
					scrollbarMode="showOnDemand" />
			<widget name="status" position="10,450" size="980,60"
					font="Regular;22" foregroundColor="#ffcc66" />
			<widget source="key_red" render="Label" position="10,540" size="240,40"
					backgroundColor="#9f1313" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_green" render="Label" position="260,540" size="240,40"
					backgroundColor="#1f771f" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_yellow" render="Label" position="510,540" size="240,40"
					backgroundColor="#a08500" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_blue" render="Label" position="760,540" size="230,40"
					backgroundColor="#18188b" font="Regular;22" halign="center"
					valign="center" />
		</screen>"""

	def __init__(self, session, manager):
		Screen.__init__(self, session)
		self.setTitle(_("UniFi Protect Display"))
		self._manager = manager
		self._config = settings_module.get_config()

		# The settings that decide which other rows exist. Changing one of
		# these is the only reason to rebuild the list.
		self._structural = (
			self._config.corner_mode, self._config.rotate)

		self["status"] = Label("")
		self["key_red"] = StaticText(_("Cancel"))
		self["key_green"] = StaticText(_("Save"))
		self["key_yellow"] = StaticText(_("Test connection"))
		self["key_blue"] = StaticText(_("Cameras"))

		ConfigListScreen.__init__(self, [], session=session,
			on_change=self._changed)
		self._build_list()

		# No "ok" here on purpose. ConfigListScreen binds OK to "select", which
		# opens the current setting for editing; taking OK over to mean
		# save-and-close would break every selection and text field.
		self["actions"] = ActionMap(
			["SetupActions", "ColorActions"], {
				"cancel": self.keyCancel,
				"save": self.keySave,
				"red": self.keyCancel,
				"green": self.keySave,
				"yellow": self._test_connection,
				"blue": self._open_cameras,
			}, -2)

	# -- the list ---------------------------------------------------------

	def _build_list(self):
		cfg = self._config
		entries = [
			_divider(_("UniFi console")),
			(_("Console address"), cfg.host,
				_("IP address or hostname of the UniFi console, for example "
					"192.168.1.1")),
			(_("API key"), cfg.apikey,
				_("Create one in Protect under Control Plane > Integrations. "
					"It is stored in plain text in the receiver's settings "
					"file.")),
			(_("Verify the TLS certificate"), cfg.verify_ssl,
				_("Leave this off. A UniFi console uses a self-signed "
					"certificate, so verification makes every request fail.")),
			_divider(_("Corner display")),
			(_("Corner mode"), cfg.corner_mode, self._mode_help()),
		]

		if cfg.corner_mode.value != MODE_OFF:
			entries.extend([
				(_("Position"), cfg.corner,
					_("Which corner of the screen the camera appears in.")),
				(_("Width"), cfg.corner_width,
					_("Width as a percentage of the screen. The height "
						"follows at 16:9.")),
				(_("Margin"), cfg.corner_margin,
					_("Distance from the screen edge, as a percentage.")),
			])
		if cfg.corner_mode.value == MODE_SNAPSHOT:
			entries.extend([
				(_("Refresh every"), cfg.snapshot_interval,
					_("Seconds between snapshots. Lower is smoother but asks "
						"more of the console.")),
				(_("Request high quality snapshots"), cfg.snapshot_high_quality,
					_("Off is usually right: the picture is scaled into a "
						"corner anyway, and a larger image costs console and "
						"decoding time.")),
			])

		entries.extend([
			_divider(_("Cameras and playback")),
			(_("Stream quality"), cfg.quality,
				_("Which Protect stream tier to use. Low or medium is H.264 "
					"on most cameras and is the safer choice on older "
					"boxes.")),
			(_("Player"), cfg.player,
				_("Automatic picks exteplayer3 when serviceapp is installed, "
					"otherwise GStreamer.")),
			(_("Use unencrypted RTSP"), cfg.plain_rtsp,
				_("Rewrites the stream URL to port 7447 without TLS. Try "
					"this only if playback fails with a TLS error; the plain "
					"port does not exist on every Protect version.")),
			_divider(_("Rotation")),
			(_("Rotate through cameras"), cfg.rotate,
				_("Cycle through the selected cameras automatically.")),
		])
		if cfg.rotate.value:
			entries.append((_("Change camera every"), cfg.rotate_interval,
				_("Seconds each camera stays on screen.")))

		entries.extend([
			_divider(_("Other")),
			(_("Start the corner automatically"), cfg.autostart,
				_("Show the corner as soon as the receiver has started.")),
			(_("Write debug messages to the log"), cfg.debug,
				_("Turn on only while troubleshooting.")),
		])

		self["config"].list = entries
		self._update_status()

	def _mode_help(self):
		caps = self._manager.capabilities
		blocker = blocker_text(caps.remux_pip_blockers())
		if blocker:
			# Naming the obstacle beats hiding the option: the user can fix
			# it, but only if they know what it is.
			return _(
				"Snapshots work on every box. Live video is unavailable "
				"here: %s.") % blocker
		return _(
			"Snapshots are still pictures, roughly one per second, and work "
			"everywhere. Live video uses the PiP decoder and ffmpeg.")

	def _changed(self):
		"""React to a changed value.

		The list is only rebuilt for the two settings that gate other rows.
		on_change fires on every keystroke, so rebuilding unconditionally
		would tear the list out from under someone typing a hostname or a
		key -- which is exactly where it would be most infuriating.
		"""
		current = self["config"].getCurrent()
		element = current[1] if current and len(current) > 1 else None
		if element not in self._structural:
			self._update_status()
			return
		self._build_list()
		if element is not None:
			for index, entry in enumerate(self["config"].list):
				if len(entry) > 1 and entry[1] is element:
					self["config"].setCurrentIndex(index)
					break

	def _update_status(self):
		cfg = self._config
		if cfg.corner_mode.value == MODE_PIP:
			blocker = blocker_text(
				self._manager.capabilities.remux_pip_blockers())
			if blocker:
				self["status"].setText(
					_("Live video will not start on this box: %s") % blocker)
				return
		if not cfg.host.value or not cfg.apikey.value:
			self["status"].setText(
				_("Enter the console address and an API key to begin."))
			return
		count = len(self._manager.store.enabled_ids())
		self["status"].setText(
			_("%d camera(s) selected. Press Blue to change.") % count)

	# -- actions ----------------------------------------------------------

	def _test_connection(self):
		"""Check host and key against the console, without saving."""
		cfg = self._config
		if not cfg.host.value or not cfg.apikey.value:
			self._status(_("Enter the console address and an API key first."))
			return
		self._status(_("Contacting the console..."))
		# Test what is on screen, not what was last saved.
		self._manager.api.host = cfg.host.value.strip()
		self._manager.api.api_key = cfg.apikey.value.strip()
		self._manager.api.verify_ssl = cfg.verify_ssl.value
		self._manager.controller.check_connection(
			on_done=self._connection_ok, on_error=self._connection_failed)

	def _connection_ok(self, info):
		version = ""
		if isinstance(info, dict):
			version = info.get("applicationVersion") or ""
		self._status(
			_("Connected. Protect version %s") % version if version
			else _("Connected."))

	def _connection_failed(self, exc):
		self._status(self._manager.controller.describe_error(exc))

	def _status(self, text):
		self["status"].setText(text)

	def _open_cameras(self):
		from .cameras import UnifiProtectCameraList
		# Save first: the camera list talks to the console, and it should
		# use the address and key currently on screen.
		self._apply()
		self.session.openWithCallback(
			self._cameras_closed, UnifiProtectCameraList, self._manager)

	def _cameras_closed(self, *args):
		self._manager.refresh_selection()
		self._update_status()

	# -- saving -----------------------------------------------------------

	def _apply(self):
		for entry in self["config"].list:
			if len(entry) > 1 and hasattr(entry[1], "save"):
				entry[1].save()
		settings_module.save()
		self._manager.reload_settings()

	def keySave(self):
		self._apply()
		self.close(True)

	def keyCancel(self):
		for entry in self["config"].list:
			if len(entry) > 1 and hasattr(entry[1], "cancel"):
				entry[1].cancel()
		self.close(False)


def _divider(text):
	"""A non-selectable heading row."""
	return (text,)
