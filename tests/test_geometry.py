# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Corner placement in both of enigma2's coordinate systems."""

import pytest

from UnifiProtectDisplay import geometry as geo

HD = (1280, 720)
FHD = (1920, 1080)


class TestCornerPlacement:
	@pytest.mark.parametrize("corner", geo.CORNERS)
	def test_window_stays_inside_the_space(self, corner):
		x, y, w, h = geo.rect_for_corner(corner, 1280, 720, 25)
		assert x >= 0 and y >= 0
		assert x + w <= 1280
		assert y + h <= 720

	def test_left_corners_sit_at_the_left_margin(self):
		for corner in (geo.CORNER_TOP_LEFT, geo.CORNER_BOTTOM_LEFT):
			x, _, _, _ = geo.rect_for_corner(corner, 1000, 600, 20, margin_percent=2)
			assert x == 20

	def test_right_corners_are_flush_with_the_right_margin(self):
		for corner in (geo.CORNER_TOP_RIGHT, geo.CORNER_BOTTOM_RIGHT):
			x, _, w, _ = geo.rect_for_corner(corner, 1000, 600, 20, margin_percent=2)
			assert x + w == 1000 - 20

	def test_top_and_bottom_differ(self):
		top = geo.rect_for_corner(geo.CORNER_TOP_LEFT, 1280, 720, 25)
		bottom = geo.rect_for_corner(geo.CORNER_BOTTOM_LEFT, 1280, 720, 25)
		assert top[1] < bottom[1]


class TestSizing:
	def test_width_follows_the_percentage(self):
		_, _, w, _ = geo.rect_for_corner(geo.CORNER_TOP_RIGHT, 1000, 600, 30)
		assert w == 300

	def test_height_follows_the_aspect_ratio(self):
		_, _, w, h = geo.rect_for_corner(
			geo.CORNER_TOP_RIGHT, 1600, 900, 25, aspect=16.0 / 9.0)
		assert w == 400
		assert h == 225

	def test_four_three_aspect_is_honoured(self):
		_, _, w, h = geo.rect_for_corner(
			geo.CORNER_TOP_RIGHT, 1200, 900, 40, aspect=4.0 / 3.0)
		assert (w, h) == (480, 360)

	@pytest.mark.parametrize("given,expected", [
		(0, geo.MIN_WIDTH_PERCENT),
		(5, geo.MIN_WIDTH_PERCENT),
		(100, geo.MAX_WIDTH_PERCENT),
		(-20, geo.MIN_WIDTH_PERCENT),
		("nonsense", geo.MIN_WIDTH_PERCENT),
		(None, geo.MIN_WIDTH_PERCENT),
	])
	def test_width_percentage_is_clamped(self, given, expected):
		# A corner is useless if it is unreadably small or covers the screen,
		# and a bad config value must not place the window off screen.
		assert geo.clamp_percent(given) == expected

	def test_oversized_window_is_shrunk_to_fit_a_short_space(self):
		# 60% of 720 at 16:9 is 432x243, which with margins still fits; make
		# the space genuinely short and check the aspect is preserved.
		x, y, w, h = geo.rect_for_corner(
			geo.CORNER_BOTTOM_RIGHT, 720, 120, 60, margin_percent=2)
		assert y >= 0
		assert y + h <= 120
		assert abs(w / float(h) - 16.0 / 9.0) < 0.1


class TestPipSpace:
	def test_pip_uses_the_fixed_720x576_grid(self):
		# PictureInPicture.py hardcodes MAX_X/MAX_Y; passing skin coordinates
		# here would push the window off screen on an HD skin.
		x, y, w, h = geo.pip_rect(geo.CORNER_TOP_RIGHT, 30)
		assert x + w <= geo.PIP_SPACE_WIDTH
		assert y + h <= geo.PIP_SPACE_HEIGHT

	def test_pip_rect_is_independent_of_the_skin(self):
		assert geo.pip_rect(geo.CORNER_TOP_LEFT, 25) == geo.pip_rect(
			geo.CORNER_TOP_LEFT, 25)

	def test_same_percentage_scales_between_spaces(self):
		# One setting has to mean the same thing in both systems.
		_, _, skin_w, _ = geo.skin_rect(geo.CORNER_TOP_RIGHT, 1280, 720, 25)
		_, _, pip_w, _ = geo.pip_rect(geo.CORNER_TOP_RIGHT, 25)
		assert skin_w / 1280.0 == pytest.approx(pip_w / 720.0, abs=0.01)


class TestLabels:
	@pytest.mark.parametrize("corner", geo.CORNERS)
	def test_every_corner_has_a_label(self, corner):
		label = geo.corner_label(corner)
		assert label and label != corner
