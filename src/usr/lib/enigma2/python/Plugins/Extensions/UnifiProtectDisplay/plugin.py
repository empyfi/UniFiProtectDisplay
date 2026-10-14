# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plugin entry points.

Deliberately thin: everything it does is create or find the
:class:`~manager.DisplayManager` and hand off. Keeping the descriptors free
of logic means the behaviour can be tested through the manager rather than
through enigma2's plugin loader.

Four places in the UI:

* the plugin menu -- setup;
* the extensions menu -- switch the corner on or off, open fullscreen, and
  skip to the next camera;
* session start -- create the manager and, if configured, bring the corner up;
* autostart with ``REASON_STOP`` -- shut the worker pool down. Session start
  alone is not enough, because enigma2 only ever delivers the *start* half of
  ``WHERE_SESSIONSTART``.
"""

from enigma import eTimer

from Plugins.Plugin import PluginDescriptor
from Screens.MessageBox import MessageBox

from .manager import DisplayManager
from .settings import MODE_OFF, init_config
from .version import PLUGIN_NAME, PLUGIN_VERSION
from .localisation import _

DESCRIPTION = _("Show UniFi Protect cameras in a corner or fullscreen")

# Names of the two descriptors the PiP key is bound to. They are looked up by
# name when the binding is computed, so they must match exactly -- hence
# constants rather than literals repeated in two modules.
KEY_SHORT_NAME = _("UniFi Protect: PiP key")
KEY_LONG_NAME = _("UniFi Protect: PiP key (long)")

# The GUI is not settled the instant a session starts, and an overlay created
# too early can be skinned against a desktop that is still changing.
AUTOSTART_DELAY_MS = 8000

_autostart_timer = None


# -- entry points ---------------------------------------------------------

def open_main(session, **kwargs):
	"""The plugin's own menu: every action in one place.

	The plugin browser is in the main menu on every image, whereas the key
	that opens the extensions menu is not the same on every box. Pointing
	the plugin-menu entry here rather than straight at setup means there is
	always a dependable way to actually start the display.
	"""
	from .ui.mainmenu import UnifiProtectMain
	manager = DisplayManager.get(session)
	session.open(UnifiProtectMain, manager)


def open_setup(session, **kwargs):
	from .ui.setup import UnifiProtectSetup
	manager = DisplayManager.get(session)
	session.open(UnifiProtectSetup, manager)


def toggle_corner(session, **kwargs):
	manager = DisplayManager.get(session)
	ok, message = manager.toggle_corner()
	if not ok and message:
		# Every refusal from the manager names something actionable, so it
		# is worth a dialog rather than only a log line.
		session.open(MessageBox, message, type=MessageBox.TYPE_INFO, timeout=8)


def open_fullscreen(session, **kwargs):
	manager = DisplayManager.get(session)
	ok, message = manager.open_fullscreen()
	if not ok and message:
		session.open(MessageBox, message, type=MessageBox.TYPE_INFO, timeout=8)


def next_camera(session, **kwargs):
	DisplayManager.get(session).next_camera()


def pip_key_short(session, **kwargs):
	"""Short press: the box's own PiP, or switch everything off."""
	_report(session, DisplayManager.get(session).pip_key_short())


def pip_key_long(session, **kwargs):
	"""Long press: camera corner on, or step to the next camera."""
	_report(session, DisplayManager.get(session).pip_key_long())


def _report(session, outcome):
	ok, message = outcome
	if not ok and message:
		session.open(MessageBox, message, type=MessageBox.TYPE_INFO, timeout=8)


# -- lifecycle ------------------------------------------------------------

def session_start(reason, session=None, **kwargs):
	"""Create the manager and optionally bring the corner up."""
	global _autostart_timer
	if reason != 0 or session is None:
		return
	init_config()
	manager = DisplayManager.get(session)
	config = _config()

	# The index ButtonSetup uses for a plugin depends on what else is
	# registered, so the binding is recomputed on every start rather than
	# stored. This also makes a setting written from outside enigma2 -- by
	# tools/configure.py, say -- take effect on the next restart.
	if config.pip_key.value:
		manager.apply_key_binding()
	manager.start_webhook()

	if not config.autostart.value or config.corner_mode.value == MODE_OFF:
		return

	def start():
		ok, message = manager.start_corner()
		if not ok:
			# Autostart failures go to the log only. A dialog appearing by
			# itself minutes after switch-on, over whatever the user is
			# watching, would be worse than the missing corner.
			manager.log.error("autostart did not start the corner: %s", message)

	_autostart_timer = eTimer()
	_autostart_timer.callback.append(start)
	_autostart_timer.start(AUTOSTART_DELAY_MS, True)


def autostart(reason, **kwargs):
	"""Shut down on enigma2 exit.

	Worker threads are daemons so they cannot block the exit, but the ffmpeg
	children started by the remux proxy are not: without this they would be
	orphaned and keep pulling from the cameras.
	"""
	if reason == 1 and DisplayManager.instance is not None:
		DisplayManager.instance.shutdown()


# -- registration ---------------------------------------------------------

def Plugins(**kwargs):
	init_config()
	return [
		PluginDescriptor(
			name=PLUGIN_NAME,
			description="%s (%s)" % (DESCRIPTION, PLUGIN_VERSION),
			where=PluginDescriptor.WHERE_PLUGINMENU,
			icon="plugin.png",
			fnc=open_main,
			needsRestart=False),
		PluginDescriptor(
			name=_("UniFi Protect: corner"),
			description=_("Switch the camera corner on or off"),
			where=PluginDescriptor.WHERE_EXTENSIONSMENU,
			fnc=toggle_corner,
			needsRestart=False),
		PluginDescriptor(
			name=_("UniFi Protect: fullscreen"),
			description=_("Show a camera full screen"),
			where=PluginDescriptor.WHERE_EXTENSIONSMENU,
			fnc=open_fullscreen,
			needsRestart=False),
		PluginDescriptor(
			name=_("UniFi Protect: next camera"),
			description=_("Skip to the next selected camera"),
			where=PluginDescriptor.WHERE_EXTENSIONSMENU,
			fnc=next_camera,
			needsRestart=False),
		# Bound to the remote's PiP key, short and long press. Registered
		# for the button setup so they can also be assigned by hand, to a
		# different key or on a box where the automatic binding cannot work
		# out which key is which.
		PluginDescriptor(
			name=KEY_SHORT_NAME,
			description=_("Picture in Picture, or switch everything off"),
			where=PluginDescriptor.WHERE_BUTTONSETUP,
			fnc=pip_key_short,
			needsRestart=False),
		PluginDescriptor(
			name=KEY_LONG_NAME,
			description=_("Camera corner on, or step to the next camera"),
			where=PluginDescriptor.WHERE_BUTTONSETUP,
			fnc=pip_key_long,
			needsRestart=False),
		PluginDescriptor(
			where=PluginDescriptor.WHERE_SESSIONSTART,
			fnc=session_start,
			needsRestart=False),
		PluginDescriptor(
			where=PluginDescriptor.WHERE_AUTOSTART,
			fnc=autostart,
			needsRestart=False),
	]


def _config():
	from .settings import get_config
	return get_config()
