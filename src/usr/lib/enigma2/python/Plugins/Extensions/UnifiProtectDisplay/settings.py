# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Configuration, and the bridge from enigma2's config objects to plain values.

This is the only module that both imports enigma2's config system and is
imported by the rest of the plugin, which keeps ``from Components.config
import ...`` out of the testable layer. :func:`build_settings` converts the
live config into a :class:`~controller.Settings`, so the decision layer never
sees a config object.

A note on the API key: enigma2 stores every ``config.*`` value as plain text
in ``/etc/enigma2/settings``. ``ConfigPassword`` censors it on screen but does
not encrypt it at rest, and there is no key store on these images to use
instead. The practical consequence -- anyone with filesystem access to the
receiver can read the key -- is documented in ``docs/install.md`` so the user
can decide what scope to give the key in Protect.
"""

from Components.config import (
	config, ConfigInteger, ConfigPassword, ConfigSelection,
	ConfigSelectionNumber, ConfigSubsection, ConfigText, ConfigYesNo)

from .controller import Settings
from .geometry import (
	CORNER_BOTTOM_LEFT, CORNER_BOTTOM_RIGHT, CORNER_TOP_LEFT,
	CORNER_TOP_RIGHT, MAX_WIDTH_PERCENT, MIN_WIDTH_PERCENT)
from .localisation import _

# Corner display modes.
MODE_OFF = "off"
MODE_SNAPSHOT = "snapshot"
MODE_PIP = "pip"

QUALITY_CHOICES = [
	("high", _("High")),
	("medium", _("Medium")),
	("low", _("Low")),
]

CORNER_CHOICES = [
	(CORNER_TOP_RIGHT, _("Top right")),
	(CORNER_TOP_LEFT, _("Top left")),
	(CORNER_BOTTOM_RIGHT, _("Bottom right")),
	(CORNER_BOTTOM_LEFT, _("Bottom left")),
]

PLAYER_CHOICES = [
	("auto", _("Automatic")),
	("exteplayer3", _("exteplayer3 (serviceapp)")),
	("gstreamer", _("GStreamer")),
	("gstplayer", _("gstplayer (serviceapp)")),
]

_initialised = False


def init_config():
	"""Create the config tree. Safe to call more than once."""
	global _initialised
	if _initialised:
		return config.plugins.unifiprotectdisplay

	config.plugins.unifiprotectdisplay = ConfigSubsection()
	settings = config.plugins.unifiprotectdisplay

	# -- console ----------------------------------------------------------
	settings.host = ConfigText(default="", fixed_size=False)
	settings.apikey = ConfigPassword(default="")
	# Off by default because a UniFi console presents a self-signed
	# certificate: with verification on, nothing works at all.
	settings.verify_ssl = ConfigYesNo(default=False)

	# -- streams ----------------------------------------------------------
	settings.quality = ConfigSelection(default="high", choices=QUALITY_CHOICES)
	# The documented workaround for boxes whose ffmpeg or GStreamer cannot
	# complete TLS against the console. Off by default: the plain port does
	# not exist on every Protect version.
	settings.plain_rtsp = ConfigYesNo(default=False)
	settings.player = ConfigSelection(default="auto", choices=PLAYER_CHOICES)

	# -- corner -----------------------------------------------------------
	settings.corner_mode = ConfigSelection(default=MODE_OFF, choices=[
		(MODE_OFF, _("Off")),
		(MODE_SNAPSHOT, _("Snapshots (works on every box)")),
		(MODE_PIP, _("Live video (PiP, needs ffmpeg)")),
	])
	settings.corner = ConfigSelection(
		default=CORNER_TOP_RIGHT, choices=CORNER_CHOICES)
	settings.corner_width = ConfigSelectionNumber(
		MIN_WIDTH_PERCENT, MAX_WIDTH_PERCENT, 5, default=25)
	settings.corner_margin = ConfigSelectionNumber(0, 10, 1, default=2)
	settings.snapshot_interval = ConfigSelectionNumber(1, 30, 1, default=2)
	settings.snapshot_high_quality = ConfigYesNo(default=False)

	# -- rotation ---------------------------------------------------------
	settings.rotate = ConfigYesNo(default=False)
	settings.rotate_interval = ConfigSelectionNumber(5, 300, 5, default=15)

	# -- behaviour --------------------------------------------------------
	settings.autostart = ConfigYesNo(default=False)
	settings.debug = ConfigYesNo(default=False)
	# Fixed port 0 means "ask the OS for a free one", which avoids colliding
	# with the stream server, the web interface, or another plugin.
	settings.proxy_port = ConfigInteger(default=0, limits=(0, 65535))

	_initialised = True
	return settings


def get_config():
	"""The config subsection, creating it on first use."""
	return init_config()


def build_settings(cfg=None):
	"""Snapshot the live config as a plain :class:`~controller.Settings`.

	Taken fresh whenever a screen opens, so a change in setup applies without
	the plugin holding a stale copy.
	"""
	cfg = cfg or get_config()
	return Settings(
		quality=cfg.quality.value,
		use_plain_rtsp=cfg.plain_rtsp.value,
		player=cfg.player.value,
		snapshot_high_quality=cfg.snapshot_high_quality.value,
	)


def save():
	"""Persist the subsection."""
	get_config().save()
	from Components.config import configfile
	configfile.save()
