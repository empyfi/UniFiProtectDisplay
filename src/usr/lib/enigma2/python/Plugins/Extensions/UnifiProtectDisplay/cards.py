# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pictures the plugin draws itself, to be played as video.

Two things in the channel list are not a camera feed but still have to look
like one, because a bouquet entry either plays or it does not:

* a camera that is offline -- a black card saying so beats a channel that
  simply fails, especially for someone who did not set the box up and has no
  way to tell a broken plugin from an unplugged camera;
* the grid, which is every camera at once as a single picture.

Both are rendered here as JPEG frames and handed to ffmpeg, which encodes
them into the same MPEG-TS the real cameras arrive as. The receiver cannot
tell the difference, so no special case is needed anywhere in enigma2.

A grid of live video would need N decoders and an encoder the box does not
have. A grid of stills needs neither, updates about once a second, and for
"is everything quiet" that is the better picture anyway.
"""

import io
import math

try:  # pragma: no cover - present on the receiver and on the workstation
	from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
	Image = None

# Broadcast-ish default. Kept at 720p: the cards are mostly flat colour, and
# a larger frame costs encoder time for nothing.
DEFAULT_SIZE = (1280, 720)
BACKGROUND = (12, 14, 18)
TEXT = (235, 238, 242)
MUTED = (150, 156, 164)
OFFLINE_TINT = (40, 16, 16)
JPEG_QUALITY = 82

# What shape a tile is assumed to be when there is no picture to measure.
# Every UniFi camera this plugin has met is 16:9.
TILE_ASPECT = 16.0 / 9.0


def available():
	"""Whether anything here can be drawn at all."""
	return Image is not None


def _font(size):
	"""A font at roughly ``size`` pixels, falling back to PIL's built-in.

	Receivers carry few fonts and not always at a predictable path, so a
	miss has to degrade rather than raise: the built-in bitmap font is ugly
	at large sizes but still readable, which beats no picture.
	"""
	candidates = (
		"/usr/share/fonts/nmsbd.ttf",
		"/usr/share/fonts/valis.ttf",
		"/usr/share/fonts/DejaVuSans.ttf",
		"/usr/share/fonts/truetype/DejaVuSans.ttf",
	)
	for path in candidates:
		try:
			return ImageFont.truetype(path, size)
		except Exception:  # noqa: BLE001
			continue
	try:
		return ImageFont.load_default(size)
	except Exception:  # noqa: BLE001
		return ImageFont.load_default()


def _centre(draw, text, font, box, fill=TEXT):
	"""Draw ``text`` centred in ``box`` = (x, y, width, height)."""
	x, y, width, height = box
	try:
		left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
		text_width, text_height = right - left, bottom - top
	except Exception:  # noqa: BLE001
		text_width, text_height = len(text) * 10, 20
		left = top = 0
	draw.text(
		(x + (width - text_width) // 2 - left,
			y + (height - text_height) // 2 - top),
		text, font=font, fill=fill)


def _encode(image):
	buffer = io.BytesIO()
	image.save(buffer, "JPEG", quality=JPEG_QUALITY)
	return buffer.getvalue()


def offline_card(name="", size=DEFAULT_SIZE, message="OFFLINE"):
	"""A black card naming the camera and saying it is offline.

	Served in place of the stream so the channel still plays. Someone who
	did not set this up gets an answer instead of a dead channel.
	"""
	if not available():
		return None
	width, height = size
	image = Image.new("RGB", (width, height), OFFLINE_TINT)
	draw = ImageDraw.Draw(image)
	_centre(draw, message, _font(max(28, height // 8)),
		(0, int(height * 0.32), width, int(height * 0.2)))
	if name:
		# Dimmer than the word OFFLINE: which camera it is matters less
		# than the fact that it is down.
		_centre(draw, name, _font(max(18, height // 18)),
			(0, int(height * 0.54), width, int(height * 0.12)), fill=MUTED)
	return _encode(image)


def message_card(text, size=DEFAULT_SIZE):
	"""A plain card, for states that are neither a camera nor an error."""
	if not available():
		return None
	width, height = size
	image = Image.new("RGB", (width, height), BACKGROUND)
	draw = ImageDraw.Draw(image)
	_centre(draw, text, _font(max(22, height // 14)),
		(0, int(height * 0.42), width, int(height * 0.16)))
	return _encode(image)


def grid_layout(count):
	"""Columns and rows for ``count`` cameras.

	The columns come from the square root and the rows follow from the
	count, so the grid is as square as it can be without keeping a row it
	has nothing to put in. Two cameras in a 2x2 sit across the top with the
	bottom half black, which reads as a broken picture rather than a sparse
	one -- and two cameras is the commonest installation there is.
	"""
	if count <= 1:
		return 1, 1
	columns = int(math.ceil(math.sqrt(count)))
	rows = int(math.ceil(count / float(columns)))
	return columns, rows


def _tile_rect(x, y, cell_width, cell_height, aspect=TILE_ASPECT):
	"""The box a tile occupies inside its cell: ``aspect``, centred.

	A camera still and an offline tile both use it, so one live camera
	beside one dead one gives two boxes of the same size instead of a
	letterboxed picture next to a full-height block of colour.
	"""
	width = cell_width
	height = int(round(width / aspect))
	if height > cell_height:
		height = cell_height
		width = int(round(height * aspect))
	return (x + (cell_width - width) // 2, y + (cell_height - height) // 2,
		width, height)


def grid_card(tiles, size=DEFAULT_SIZE, gap=4):
	"""Compose one picture from several camera stills.

	``tiles`` is ``[(name, jpeg_bytes_or_None), ...]``; a tile without an
	image is drawn as its own small offline card, so one unreachable camera
	does not blank the whole grid or shift the others.
	"""
	if not available():
		return None
	width, height = size
	columns, rows = grid_layout(len(tiles))
	image = Image.new("RGB", (width, height), BACKGROUND)
	draw = ImageDraw.Draw(image)

	cell_width = (width - gap * (columns + 1)) // columns
	cell_height = (height - gap * (rows + 1)) // rows
	# Sized from the tile rather than the cell: in a wide-cell layout the
	# cell is much taller than the picture in it, and a font scaled to the
	# cell would dwarf the camera.
	label_font = _font(max(12, _tile_rect(0, 0, cell_width, cell_height)[3] // 10))

	for index, (name, data) in enumerate(tiles):
		column, row = index % columns, index // columns
		# A last row with gaps in it is centred, so three cameras are a row
		# of two above one in the middle, not one hanging off to the left.
		in_row = min(columns, len(tiles) - row * columns)
		indent = (columns - in_row) * (cell_width + gap) // 2
		x = gap + indent + column * (cell_width + gap)
		y = gap + row * (cell_height + gap)

		picture = None
		if data:
			try:
				picture = _fit(Image.open(io.BytesIO(data)).convert("RGB"),
					cell_width, cell_height)
			except Exception:  # noqa: BLE001
				picture = None
		if picture is not None:
			tile_x = x + (cell_width - picture.width) // 2
			tile_y = y + (cell_height - picture.height) // 2
			tile_width, tile_height = picture.width, picture.height
			image.paste(picture, (tile_x, tile_y))
		else:
			tile_x, tile_y, tile_width, tile_height = _tile_rect(
				x, y, cell_width, cell_height)
			draw.rectangle(
				[tile_x, tile_y, tile_x + tile_width, tile_y + tile_height],
				fill=OFFLINE_TINT)
			_centre(draw, "OFFLINE", label_font,
				(tile_x, tile_y, tile_width, tile_height))
		# The name goes on a band along the bottom of the picture -- of the
		# picture, not of the cell, or it floats in black somewhere below a
		# letterboxed tile with nothing to attach it to.
		band = max(16, tile_height // 8)
		draw.rectangle(
			[tile_x, tile_y + tile_height - band,
				tile_x + tile_width, tile_y + tile_height],
			fill=(0, 0, 0))
		_centre(draw, name or "", label_font,
			(tile_x, tile_y + tile_height - band, tile_width, band))
	return _encode(image)


def _fit(tile, width, height):
	"""Scale ``tile`` into the cell, keeping its proportions."""
	scale = min(width / float(tile.width), height / float(tile.height))
	new_size = (max(1, int(tile.width * scale)), max(1, int(tile.height * scale)))
	try:
		return tile.resize(new_size, Image.LANCZOS)
	except Exception:  # noqa: BLE001
		return tile.resize(new_size)
