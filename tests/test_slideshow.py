# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rotation as a channel of stills.

Two numbers decide how this feels and both are tested here: how often the
camera changes, and how often the picture of that camera is renewed. The
second is the one that costs a request to the console every time, so a
test that it is not asked more often than it should be is a test about the
console as much as about the television.
"""

import pytest

from UnifiProtectDisplay.rotation import Rotation
from UnifiProtectDisplay.slideshow import SlideRotation


class Spy:
	"""Stands in for the console and the renderer."""

	def __init__(self, connected=None, broken=()):
		self.fetches = []
		self.renders = []
		self._connected = connected
		self._broken = set(broken)

	def fetch(self, camera_id):
		self.fetches.append(camera_id)
		if camera_id in self._broken:
			raise RuntimeError("console said no")
		return ("jpeg:" + camera_id).encode()

	def describe(self, camera_id):
		connected = True
		if self._connected is not None:
			connected = camera_id in self._connected
		return "Cam " + camera_id, connected

	def render(self, name, data):
		self.renders.append((name, data))
		if name is None:
			return b"nothing-selected"
		return b"frame:" + (data or b"offline") + b"|" + name.encode()


def make(ids=("a", "b", "c"), interval=15.0, refresh=2.0, **kwargs):
	spy = Spy(**kwargs)
	show = SlideRotation(
		Rotation(list(ids)), spy.fetch, spy.describe, spy.render,
		interval=interval, refresh=refresh)
	return show, spy


class TestChangingCamera:
	def test_the_first_frame_is_the_first_camera(self):
		show, spy = make()
		assert show.frame(now=100.0) == b"frame:jpeg:a|Cam a"
		assert show.camera == "a"

	def test_the_camera_holds_for_the_interval(self):
		show, _spy = make(interval=15.0)
		show.frame(now=100.0)
		show.frame(now=114.9)
		assert show.camera == "a"

	def test_the_next_camera_comes_up_when_it_is_due(self):
		show, _spy = make(interval=15.0)
		show.frame(now=100.0)
		assert show.frame(now=115.0) == b"frame:jpeg:b|Cam b"
		assert show.frame(now=130.0) == b"frame:jpeg:c|Cam c"

	def test_it_wraps_round(self):
		show, _spy = make(interval=10.0)
		for moment in (0.0, 10.0, 20.0):
			show.frame(now=moment)
		assert show.frame(now=30.0) == b"frame:jpeg:a|Cam a"

	def test_a_change_of_camera_is_never_a_stale_picture(self):
		# The refresh window must not suppress the frame that shows the
		# camera has changed, or the new camera arrives late by as much
		# as a whole refresh.
		show, _spy = make(interval=10.0, refresh=5.0)
		show.frame(now=0.0)
		assert show.frame(now=10.0) == b"frame:jpeg:b|Cam b"

	def test_one_camera_stays_put(self):
		show, _spy = make(ids=("a",), interval=10.0)
		assert show.frame(now=0.0) == b"frame:jpeg:a|Cam a"
		show.frame(now=10.0)
		assert show.camera == "a"


class TestRenewingThePicture:
	def test_the_picture_is_kept_inside_the_refresh_window(self):
		show, spy = make(refresh=2.0)
		first = show.frame(now=100.0)
		assert show.frame(now=101.0) == first
		assert spy.fetches == ["a"]

	def test_there_is_always_a_picture_to_hand(self):
		# The proxy asks a route for a frame to decide whether the route
		# exists, so "nothing has changed" must not look like "no such
		# channel" -- it served a 404 to the second viewer when it did.
		show, _spy = make(refresh=60.0)
		assert show.frame(now=0.0) is not None
		assert show.frame(now=1.0) is not None
		assert show.frame(now=2.0) is not None

	def test_it_is_renewed_once_the_window_is_up(self):
		show, spy = make(refresh=2.0)
		show.frame(now=100.0)
		show.frame(now=102.0)
		assert spy.fetches == ["a", "a"]

	def test_the_console_is_asked_once_per_refresh_not_once_per_frame(self):
		# The feeder calls this at the encoder's rate. At two or four
		# frames a second, one request each would be a denial of service
		# aimed at the user's own console.
		show, spy = make(interval=60.0, refresh=1.0)
		moment = 0.0
		while moment < 10.0:
			show.frame(now=moment)
			moment += 0.25
		assert len(spy.fetches) == 10

	def test_offline_cameras_are_not_asked_for_a_picture(self):
		show, spy = make(connected={"a", "c"}, interval=10.0)
		show.frame(now=0.0)
		assert show.frame(now=10.0) == b"frame:offline|Cam b"
		assert spy.fetches == ["a"]


class TestWhenThingsGoWrong:
	def test_an_offline_camera_keeps_its_turn(self):
		# Stepping over it is right for the corner, where it would be a
		# blank screen. Here it is a card naming the camera that is down.
		show, _spy = make(connected={"a"}, interval=10.0)
		show.frame(now=0.0)
		show.frame(now=10.0)
		assert show.camera == "b"

	def test_a_console_that_blinks_does_not_blank_the_television(self):
		show, _spy = make(ids=("a",), interval=60.0, refresh=1.0, broken={"a"})
		show._cursor.set_cameras(["a"])
		first = show.frame(now=0.0)
		assert first is not None
		# Still the same picture, not a blank one.
		assert show.frame(now=5.0) == first

	def test_the_first_fetch_failing_still_gives_a_picture(self):
		show, _spy = make(ids=("a",), broken={"a"})
		frame = show.frame(now=0.0)
		assert frame == b"frame:offline|Cam a"

	def test_no_cameras_selected_says_so(self):
		show, spy = make(ids=())
		assert show.frame(now=0.0) == b"nothing-selected"
		assert spy.fetches == []

	def test_reset_starts_again_from_the_top(self):
		show, _spy = make(interval=10.0)
		show.frame(now=0.0)
		show.frame(now=10.0)
		show.reset()
		assert show.frame(now=20.0) == b"frame:jpeg:a|Cam a"


class TestTiming:
	@pytest.mark.parametrize("refresh,seconds,expected", [
		(1.0, 15.0, 15),
		(2.0, 15.0, 8),
		(5.0, 15.0, 3),
	])
	def test_how_many_pictures_one_turn_gets(self, refresh, seconds, expected):
		# The answer to "how often does it update": once per refresh, for
		# as long as the camera's turn lasts.
		show, spy = make(interval=seconds, refresh=refresh)
		moment = 0.0
		while moment < seconds:
			show.frame(now=moment)
			moment += 0.25
		assert len(spy.fetches) == expected
