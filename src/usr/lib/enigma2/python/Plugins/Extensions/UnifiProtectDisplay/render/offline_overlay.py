# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Saying "offline" instead of showing nothing.

A camera the console reports as unreachable decodes to nothing, and a black
rectangle is indistinguishable from a bug -- which is exactly how this
started life as a bug report. Skipping such cameras was the first answer and
the wrong one: it hides that a camera exists and is down, which is precisely
what someone watching a surveillance corner wants to know.

So the camera keeps its place and is labelled. This is an ordinary Screen
drawn over the corner rectangle, not something the video decoder does, so it
works the same whether the corner is live video or snapshots, and it also
covers whatever stale frame the decoder was still holding.
"""

from Components.Label import Label
from Screens.Screen import Screen

from ..localisation import _

# Opaque black. In enigma2 a colour is #TTRRGGBB where TT is transparency,
# so 00 is fully opaque -- the point here is to cover the picture, not to
# let it show through.
BACKGROUND = "#00000000"
TEXT_COLOUR = "#00ffffff"


class OfflineOverlay(Screen):
	"""Black panel with the camera's name and "offline", over the corner."""

	def __init__(self, session, rect, name=""):
		x, y, width, height = rect
		self.skin = _build_skin(x, y, width, height)
		Screen.__init__(self, session)
		self["name"] = Label(name or "")
		self["state"] = Label(_("offline"))

	def set_camera(self, name):
		self["name"].setText(name or "")


def _build_skin(x, y, width, height):
	"""Skin for one corner-sized panel.

	Built here because the corner's position and size are user settings and
	a skin file cannot read the configuration. Font sizes scale with the
	panel so the word stays readable in a small corner and does not look
	absurd in a large one.
	"""
	state_size = max(14, min(40, height // 4))
	name_size = max(12, min(26, height // 7))
	name_height = name_size + 6
	return """
		<screen name="UnifiProtectOffline" position="%d,%d" size="%d,%d"
				flags="wfNoBorder" backgroundColor="%s" zPosition="12" title=" ">
			<widget name="state" position="0,%d" size="%d,%d"
					font="Regular;%d" halign="center" valign="center"
					transparent="1" foregroundColor="%s" zPosition="1" />
			<widget name="name" position="0,%d" size="%d,%d"
					font="Regular;%d" halign="center" valign="center"
					transparent="1" foregroundColor="%s" zPosition="1" />
		</screen>""" % (
		x, y, width, height, BACKGROUND,
		max(0, (height - state_size - name_height) // 2), width, state_size + 8,
		state_size, TEXT_COLOUR,
		max(0, (height + state_size) // 2), width, name_height,
		name_size, TEXT_COLOUR)
