# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plugin's own menu, and the reliable way in.

The extensions menu is the natural home for "switch the corner on", but the
key that opens it is not the same on every box: openATV images and skins
rebind the colour buttons freely, and on some receivers blue opens the quick
start menu instead. A user who cannot find that menu cannot start the plugin
at all, however well the rest of it works.

The plugin browser, by contrast, is in the main menu on every image. Pointing
the plugin-menu entry at this screen rather than straight at setup means
there is always one dependable route to every action, with setup as one item
among them. The extensions entries stay as shortcuts for boxes where they
are convenient.
"""

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.Sources.StaticText import StaticText
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen

from ..localisation import _, blocker_text
from ..settings import MODE_OFF, MODE_PIP, MODE_SNAPSHOT, get_config

ACTION_TOGGLE = "toggle"
ACTION_FULLSCREEN = "fullscreen"
ACTION_NEXT = "next"
ACTION_CAMERAS = "cameras"
ACTION_SETUP = "setup"
ACTION_WINDOW = "window"


class UnifiProtectMain(Screen):
	skin = """
		<screen name="UnifiProtectMain" position="center,center" size="820,520"
				title="UniFi Protect Display">
			<widget name="list" position="10,10" size="800,280"
					scrollbarMode="showOnDemand" font="Regular;28"
					itemHeight="46" />
			<widget name="status" position="10,300" size="800,110"
					font="Regular;22" foregroundColor="#cccccc" />
			<widget source="key_red" render="Label" position="10,460" size="195,40"
					backgroundColor="#9f1313" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_green" render="Label" position="215,460" size="195,40"
					backgroundColor="#1f771f" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_yellow" render="Label" position="420,460" size="195,40"
					backgroundColor="#a08500" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_blue" render="Label" position="625,460" size="185,40"
					backgroundColor="#18188b" font="Regular;22" halign="center"
					valign="center" />
		</screen>"""

	def __init__(self, session, manager):
		Screen.__init__(self, session)
		self.setTitle(_("UniFi Protect Display"))
		self._manager = manager
		self._actions = []

		self["list"] = MenuList([])
		self["status"] = Label("")
		self["key_red"] = StaticText(_("Close"))
		self["key_green"] = StaticText(_("Settings"))
		self["key_yellow"] = StaticText(_("Cameras"))
		self["key_blue"] = StaticText(_("Corner on/off"))

		self["actions"] = ActionMap(
			["OkCancelActions", "ColorActions"], {
				"ok": self._activate,
				"cancel": self.close,
				"red": self.close,
				"green": self._open_setup,
				"yellow": self._open_cameras,
				"blue": self._toggle_corner,
			}, -1)

		self.onLayoutFinish.append(self._rebuild)

	# -- the list ---------------------------------------------------------

	def _rebuild(self):
		config = get_config()
		running = self._manager.corner_running()
		mode = config.corner_mode.value

		rows = []
		self._actions = []

		if mode == MODE_OFF:
			rows.append(_("Corner is switched off in settings"))
			self._actions.append(ACTION_SETUP)
		else:
			label = _("Hide corner") if running else _("Show corner")
			rows.append("%s  (%s)" % (label, _("live video")
				if mode == MODE_PIP else _("snapshots")))
			self._actions.append(ACTION_TOGGLE)

		# Only while the live-video corner is up: enigma2's PiPSetup edits a
		# running PiP window and has nothing to work on otherwise. Offered
		# here because openATV has no menu entry for PiP geometry at all --
		# its only route is an extensions entry that does not appear on
		# every box.
		if running and mode == MODE_PIP:
			rows.append(_("Move and resize the window"))
			self._actions.append(ACTION_WINDOW)

		rows.append(_("Show a camera full screen"))
		self._actions.append(ACTION_FULLSCREEN)

		rows.append(_("Next camera"))
		self._actions.append(ACTION_NEXT)

		rows.append(_("Cameras..."))
		self._actions.append(ACTION_CAMERAS)

		rows.append(_("Settings..."))
		self._actions.append(ACTION_SETUP)

		index = self["list"].getCurrentIndex() or 0
		self["list"].setList(rows)
		self["list"].setCurrentIndex(min(index, len(rows) - 1))
		self["key_blue"] = StaticText(
			_("Hide corner") if running else _("Show corner"))
		self._update_status()

	def _update_status(self):
		config = get_config()
		lines = []

		if not config.host.value or not config.apikey.value:
			lines.append(_("Not set up yet: no console address or API key."))
		else:
			count = len(self._manager.store.enabled_ids())
			lines.append(_("Console %s, %d camera(s) selected.")
				% (config.host.value, count))

		mode = config.corner_mode.value
		if mode == MODE_PIP:
			blocker = blocker_text(
				self._manager.capabilities.remux_pip_blockers())
			if blocker:
				lines.append(_("Live video cannot run here: %s") % blocker)
			else:
				lines.append(_("Corner: live video, %s")
					% self._corner_description())
		elif mode == MODE_SNAPSHOT:
			lines.append(_("Corner: snapshots every %s s, %s")
				% (config.snapshot_interval.value, self._corner_description()))
		else:
			lines.append(_("Corner: off"))

		self["status"].setText("\n".join(lines))

	def _corner_description(self):
		from .. import geometry
		config = get_config()
		if config.follow_pip.value:
			return _("same window as Picture in Picture")
		return _("%s, %s%% wide") % (
			geometry.corner_label(config.corner.value),
			config.corner_width.value)

	# -- actions ----------------------------------------------------------

	def _activate(self):
		index = self["list"].getCurrentIndex()
		if index is None or index < 0 or index >= len(self._actions):
			return
		action = self._actions[index]
		if action == ACTION_TOGGLE:
			self._toggle_corner()
		elif action == ACTION_FULLSCREEN:
			self._open_fullscreen()
		elif action == ACTION_NEXT:
			self._manager.next_camera()
			self._rebuild()
		elif action == ACTION_CAMERAS:
			self._open_cameras()
		elif action == ACTION_WINDOW:
			self._edit_window()
		elif action == ACTION_SETUP:
			self._open_setup()

	def _toggle_corner(self):
		ok, message = self._manager.toggle_corner()
		if not ok and message:
			self.session.open(
				MessageBox, message, type=MessageBox.TYPE_INFO, timeout=10)
		self._rebuild()
		if ok and self._manager.corner_running():
			# The corner is behind this screen, so there is nothing to see
			# until it closes. Say so rather than leaving the user pressing
			# the button again.
			self.close()

	def _edit_window(self):
		"""Hand the running PiP window to enigma2's own geometry editor.

		Arrow keys move it, Bouquet +/- resize it, OK saves into
		config.av.pip -- which is also what the box's own Picture-in-Picture
		uses, so one edit arranges both.
		"""
		corner = self._manager.pip_corner()
		pip = getattr(self.session, "pip", None)
		if corner is None or pip is None:
			self.session.open(
				MessageBox,
				_("Switch the corner on first, then the window can be "
					"moved and resized."),
				type=MessageBox.TYPE_INFO, timeout=8)
			return
		try:
			from Screens.PiPSetup import PiPSetup
		except ImportError as exc:
			self.session.open(
				MessageBox,
				_("This image has no Picture in Picture editor (%s).") % exc,
				type=MessageBox.TYPE_INFO, timeout=8)
			return
		self.session.openWithCallback(self._window_edited, PiPSetup, pip=pip)

	def _window_edited(self, *args):
		# PiPSetup sizes the video to fill, so whichever fit the user chose
		# has to be put back afterwards.
		corner = self._manager.pip_corner()
		if corner is not None:
			corner.reapply_geometry()
		self._rebuild()

	def _open_fullscreen(self):
		ok, message = self._manager.open_fullscreen()
		if not ok and message:
			self.session.open(
				MessageBox, message, type=MessageBox.TYPE_INFO, timeout=10)

	def _open_cameras(self):
		from .cameras import UnifiProtectCameraList
		self.session.openWithCallback(
			self._returned, UnifiProtectCameraList, self._manager)

	def _open_setup(self):
		from .setup import UnifiProtectSetup
		self.session.openWithCallback(
			self._returned, UnifiProtectSetup, self._manager)

	def _returned(self, *args):
		self._rebuild()
