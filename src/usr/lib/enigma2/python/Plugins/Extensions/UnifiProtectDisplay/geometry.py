# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Placing the corner window, in both coordinate systems enigma2 uses.

There are two, and they are not the same, which is the whole reason this is a
module rather than four lines inline:

* **Skin space** -- what a ``Screen`` and its widgets use. Its size is the
  skin's resolution, commonly 1280x720 or 1920x1080. The snapshot overlay
  lives here.
* **PiP space** -- a fixed 720x576 grid, regardless of skin or output
  resolution. ``Screens/PictureInPicture.py`` hardcodes ``MAX_X = 720`` and
  ``MAX_Y = 576`` and stores ``config.av.pip`` in it, so the remux PiP corner
  must be computed there and nowhere else.

Expressing the user's choice as a percentage of width keeps one setting
meaningful in both, and keeps a corner the same visual size when the user
switches skins.
"""

CORNER_TOP_LEFT = "topleft"
CORNER_TOP_RIGHT = "topright"
CORNER_BOTTOM_LEFT = "bottomleft"
CORNER_BOTTOM_RIGHT = "bottomright"

CORNERS = (
	CORNER_TOP_RIGHT,
	CORNER_TOP_LEFT,
	CORNER_BOTTOM_RIGHT,
	CORNER_BOTTOM_LEFT,
)

# PiP's own fixed coordinate space (Screens/PictureInPicture.py).
PIP_SPACE_WIDTH = 720
PIP_SPACE_HEIGHT = 576

# 16:9 is right for UniFi cameras; the aspect is configurable for the rare
# 4:3 or square-sensor model.
DEFAULT_ASPECT = 16.0 / 9.0

# Bounds on the width setting. Below ~10% nothing is recognisable on a TV at
# couch distance; above 60% it stops being a corner.
MIN_WIDTH_PERCENT = 10
MAX_WIDTH_PERCENT = 60


def clamp_percent(value, low=MIN_WIDTH_PERCENT, high=MAX_WIDTH_PERCENT):
	try:
		value = int(value)
	except (TypeError, ValueError):
		return low
	return max(low, min(high, value))


def rect_for_corner(corner, space_width, space_height, width_percent,
		margin_percent=2, aspect=DEFAULT_ASPECT):
	"""Return ``(x, y, width, height)`` for a corner window.

	``width_percent`` and ``margin_percent`` are of ``space_width``, so the
	same numbers give a visually identical result in skin space and in PiP
	space. The height follows from ``aspect``, which keeps the picture from
	being stretched when the skin is not 16:9.

	The result is clamped into the space, so an over-large width or margin
	degrades to a window flush with the edge rather than one placed partly
	off screen.
	"""
	width_percent = clamp_percent(width_percent)
	margin = max(0, int(space_width * margin_percent / 100.0))

	width = max(1, int(space_width * width_percent / 100.0))
	height = max(1, int(width / float(aspect or DEFAULT_ASPECT)))

	# A tall window in a short space (4:3 output, large width) would hang off
	# the bottom; shrink to fit and keep the aspect.
	usable_height = max(1, space_height - 2 * margin)
	if height > usable_height:
		height = usable_height
		width = max(1, int(height * float(aspect or DEFAULT_ASPECT)))
	usable_width = max(1, space_width - 2 * margin)
	if width > usable_width:
		width = usable_width
		height = max(1, int(width / float(aspect or DEFAULT_ASPECT)))

	if corner in (CORNER_TOP_LEFT, CORNER_BOTTOM_LEFT):
		x = margin
	else:
		x = space_width - width - margin
	if corner in (CORNER_TOP_LEFT, CORNER_TOP_RIGHT):
		y = margin
	else:
		y = space_height - height - margin

	return max(0, x), max(0, y), width, height


def skin_rect(corner, skin_width, skin_height, width_percent,
		margin_percent=2, aspect=DEFAULT_ASPECT):
	"""Corner rectangle in skin space, for the snapshot overlay."""
	return rect_for_corner(
		corner, skin_width, skin_height, width_percent, margin_percent, aspect)


def pip_rect(corner, width_percent, margin_percent=2, aspect=DEFAULT_ASPECT):
	"""Corner rectangle in PiP's fixed 720x576 space.

	Feed the result to ``session.pip.move(x, y)`` and
	``session.pip.resize(w, h)``; enigma2 maps it onto the real output
	itself. Passing skin coordinates here is the classic mistake -- on a
	1080p skin the window would land far off screen.
	"""
	return rect_for_corner(
		corner, PIP_SPACE_WIDTH, PIP_SPACE_HEIGHT, width_percent,
		margin_percent, aspect)


def corner_label(corner):
	"""Human-readable name, for setup and for log lines."""
	return {
		CORNER_TOP_LEFT: "top left",
		CORNER_TOP_RIGHT: "top right",
		CORNER_BOTTOM_LEFT: "bottom left",
		CORNER_BOTTOM_RIGHT: "bottom right",
	}.get(corner, corner)


# Enigma2's own default for config.av.pip, used when that entry does not
# exist yet -- it is only created the first time PictureInPicture is built.
PIP_DEFAULT_RECT = (510, 28, 180, 135)


def letterbox(outer_width, outer_height, aspect=DEFAULT_ASPECT):
	"""Centre an ``aspect``-correct rectangle inside a given one.

	Returns ``(x, y, width, height)`` relative to the outer rectangle. The
	camera keeps its proportions and the leftover becomes bars, rather than
	the picture being stretched to fill a window whose shape it does not
	share. Which way the bars fall follows from the two shapes: a 16:9
	camera in a 4:3 window gets bars above and below, the reverse gets them
	at the sides.
	"""
	outer_width = max(1, int(outer_width))
	outer_height = max(1, int(outer_height))
	aspect = float(aspect or DEFAULT_ASPECT)
	if aspect <= 0:
		aspect = DEFAULT_ASPECT

	width = outer_width
	height = max(1, int(round(width / aspect)))
	if height > outer_height:
		height = outer_height
		width = max(1, int(round(height * aspect)))

	x = (outer_width - width) // 2
	y = (outer_height - height) // 2
	return x, y, width, height


def pip_config_rect(values=None):
	"""Normalise ``config.av.pip`` into ``(x, y, width, height)``.

	Falls back to enigma2's default for anything missing or malformed, so a
	config that has never been written still yields the geometry the user
	actually sees.
	"""
	default = PIP_DEFAULT_RECT
	if not values:
		return default
	try:
		rect = tuple(int(values[index]) for index in range(4))
	except (TypeError, ValueError, IndexError, KeyError):
		return default
	if rect[2] <= 0 or rect[3] <= 0:
		return default
	return rect


def to_skin_space(rect, skin_width, skin_height):
	"""Scale a rectangle from PiP's 720x576 grid into skin coordinates.

	The snapshot overlay is an ordinary Screen and therefore lives in skin
	space, but the geometry it has to match is stored in PiP space. Without
	this conversion a window copied from config.av.pip would land at a
	quarter of its intended size on a 1080p skin.
	"""
	x, y, width, height = rect
	scale_x = float(skin_width) / PIP_SPACE_WIDTH
	scale_y = float(skin_height) / PIP_SPACE_HEIGHT
	return (
		int(round(x * scale_x)),
		int(round(y * scale_y)),
		max(1, int(round(width * scale_x))),
		max(1, int(round(height * scale_y))),
	)
