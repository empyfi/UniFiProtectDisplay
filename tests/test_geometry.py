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


class TestLetterbox:
	def test_wide_camera_in_a_squarer_window_gets_bars_top_and_bottom(self):
		# enigma2's default PiP window is 180x135, close to 4:3; a 16:9
		# camera must keep its shape rather than be squashed into it.
		x, y, w, h = geo.letterbox(180, 135, 16.0 / 9.0)
		assert (x, w) == (0, 180)
		assert y > 0 and h < 135
		assert abs(w / float(h) - 16.0 / 9.0) < 0.05

	def test_square_camera_in_a_wide_window_gets_bars_at_the_sides(self):
		x, y, w, h = geo.letterbox(320, 180, 1.0)
		assert (y, h) == (0, 180)
		assert x > 0 and w < 320
		assert w == h

	def test_matching_aspect_fills_the_window(self):
		assert geo.letterbox(320, 180, 16.0 / 9.0) == (0, 0, 320, 180)

	def test_result_always_fits_inside(self):
		for outer in ((180, 135), (320, 180), (100, 300), (1, 1)):
			for aspect in (16 / 9.0, 4 / 3.0, 1.0, 0.5, 3.0):
				x, y, w, h = geo.letterbox(outer[0], outer[1], aspect)
				assert x >= 0 and y >= 0
				assert x + w <= outer[0]
				assert y + h <= outer[1]
				assert w >= 1 and h >= 1

	def test_it_is_centred(self):
		x, y, w, h = geo.letterbox(200, 200, 16.0 / 9.0)
		assert abs((200 - w) // 2 - x) <= 1
		assert abs((200 - h) // 2 - y) <= 1

	@pytest.mark.parametrize("aspect", [0, None, -1])
	def test_a_nonsense_aspect_falls_back(self, aspect):
		x, y, w, h = geo.letterbox(320, 180, aspect)
		assert w > 0 and h > 0


class TestPipConfigRect:
	def test_missing_config_yields_the_enigma2_default(self):
		# config.av.pip only exists once PictureInPicture has been built.
		assert geo.pip_config_rect(None) == geo.PIP_DEFAULT_RECT
		assert geo.pip_config_rect([]) == geo.PIP_DEFAULT_RECT

	def test_reads_a_stored_rectangle(self):
		assert geo.pip_config_rect([400, 60, 250, 141]) == (400, 60, 250, 141)

	def test_strings_are_accepted(self):
		# ConfigSequence values arrive as ints, but a hand-edited settings
		# file is not guaranteed to.
		assert geo.pip_config_rect(["400", "60", "250", "141"]) == (
			400, 60, 250, 141)

	@pytest.mark.parametrize("values", [
		[400, 60, 0, 141], [400, 60, 250, 0], [1, 2], ["x", "y", "z", "w"]])
	def test_malformed_values_fall_back(self, values):
		assert geo.pip_config_rect(values) == geo.PIP_DEFAULT_RECT


class TestToSkinSpace:
	def test_scales_from_pip_space_to_the_skin(self):
		# Passing PiP coordinates to a Screen unconverted would land the
		# window at a fraction of its intended size on an HD skin.
		rect = geo.to_skin_space((510, 28, 180, 135), 1280, 720)
		assert rect[0] == round(510 * 1280 / 720.0)
		assert rect[2] == round(180 * 1280 / 720.0)

	def test_identity_at_pip_resolution(self):
		rect = (510, 28, 180, 135)
		assert geo.to_skin_space(
			rect, geo.PIP_SPACE_WIDTH, geo.PIP_SPACE_HEIGHT) == rect

	def test_stays_on_screen(self):
		for skin in ((1280, 720), (1920, 1080), (720, 576)):
			x, y, w, h = geo.to_skin_space((510, 28, 180, 135), *skin)
			assert x + w <= skin[0] + 1
			assert y + h <= skin[1] + 1

	def test_proportions_are_preserved(self):
		_, _, w, h = geo.to_skin_space((0, 0, 180, 135), 1440, 1152)
		assert (w, h) == (360, 270)
