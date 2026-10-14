#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate the plugin menu icon.

Kept as a script rather than a committed binary blob so the icon can be
regenerated and reviewed as a diff. Run it after changing the design:

	python tools/make_icon.py

Drawn at 4x and downsampled, which is the cheapest way to get clean edges
without hand-tuning anti-aliasing.
"""

import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(
	HERE, os.pardir, "src", "usr", "lib", "enigma2", "python", "Plugins",
	"Extensions", "UnifiProtectDisplay", "plugin.png")

# Enigma2 plugin menu icons are small and sit on a dark skin, so the design
# has to survive at 100x40: one strong shape, high contrast, no fine detail.
WIDTH, HEIGHT = 100, 40
SCALE = 4

BACKDROP = (27, 40, 56, 255)
ACCENT = (80, 170, 240, 255)
LIGHT = (240, 244, 248, 255)
DARK = (14, 20, 28, 255)


def draw(size_scale):
	width, height = WIDTH * size_scale, HEIGHT * size_scale
	image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
	pen = ImageDraw.Draw(image)

	radius = 6 * size_scale
	pen.rounded_rectangle(
		[0, 0, width - 1, height - 1], radius=radius, fill=BACKDROP)

	# Camera lens: concentric rings read clearly even when scaled down.
	cx, cy = int(width * 0.28), height // 2
	outer = int(height * 0.34)
	pen.ellipse(
		[cx - outer, cy - outer, cx + outer, cy + outer],
		fill=LIGHT)
	pen.ellipse(
		[cx - outer + 2 * size_scale, cy - outer + 2 * size_scale,
			cx + outer - 2 * size_scale, cy + outer - 2 * size_scale],
		fill=DARK)
	inner = int(outer * 0.42)
	pen.ellipse(
		[cx - inner, cy - inner, cx + inner, cy + inner], fill=ACCENT)

	# Three arcs to the right: the signal, and by implication the stream.
	for index in range(3):
		span = int(height * (0.22 + 0.17 * index))
		ax = int(width * 0.56)
		pen.arc(
			[ax - span, cy - span, ax + span, cy + span],
			start=-55, end=55,
			fill=ACCENT if index == 0 else LIGHT,
			width=max(1, int(1.6 * size_scale)))

	return image


def main():
	large = draw(SCALE)
	icon = large.resize((WIDTH, HEIGHT), Image.LANCZOS)
	os.makedirs(os.path.dirname(TARGET), exist_ok=True)
	icon.save(TARGET, "PNG", optimize=True)
	print("wrote %s (%dx%d, %d bytes)"
		% (os.path.normpath(TARGET), WIDTH, HEIGHT, os.path.getsize(TARGET)))
	return 0


if __name__ == "__main__":
	sys.exit(main())
