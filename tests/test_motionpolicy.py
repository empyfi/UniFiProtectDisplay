# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a run of motion reports does to the picture.

Protect sends a stream of separate alarms, so the interesting cases are all
sequences: the same camera reporting again while it is still on screen, two
cameras reporting within a second of each other, and one camera reporting
over and over while another one waits. Each of those broke something real
before this module existed, and each is a test here.

Time is passed in, so a twenty-second hold is tested in microseconds.
"""

import pytest

from UnifiProtectDisplay.motionpolicy import (
	EXTEND, IGNORE, MIN_DWELL, SHOW, MotionPolicy)

A = "cam-a"
B = "cam-b"


@pytest.fixture
def policy():
	return MotionPolicy(hold=20.0, min_dwell=5.0)


class TestFirstReport:
	def test_shows_the_camera(self, policy):
		assert policy.report(A, 100.0) == SHOW
		assert policy.camera == A

	def test_shows_it_even_when_something_else_is_on_screen(self, policy):
		# Rotation may have left another camera there; motion outranks it.
		assert policy.report(A, 100.0, on_screen=B) == SHOW

	def test_ignores_a_report_without_a_camera(self, policy):
		assert policy.report(None, 100.0) == IGNORE
		assert policy.report("", 100.0) == IGNORE
		assert policy.camera is None


class TestTheSameCameraAgain:
	"""The failure this was written for: alarms repeat while someone moves."""

	def test_is_not_shown_again(self, policy):
		policy.report(A, 100.0)
		assert policy.report(A, 101.0, on_screen=A) == EXTEND

	def test_still_extends_the_hold(self, policy):
		policy.report(A, 100.0)
		policy.report(A, 115.0, on_screen=A)
		# Without the second report the hold would have ended at 120.
		assert policy.holding(125.0)
		assert not policy.holding(136.0)

	def test_a_long_run_never_shows_twice(self, policy):
		assert policy.report(A, 100.0) == SHOW
		for tick in range(1, 30):
			assert policy.report(A, 100.0 + tick, on_screen=A) == EXTEND

	def test_a_camera_rotation_left_on_screen_is_only_extended(self, policy):
		# Nothing was reported yet, but A is already the picture: there is
		# no reason to restart it, and every reason not to.
		assert policy.report(A, 100.0, on_screen=A) == EXTEND
		assert policy.camera == A
		assert policy.holding(110.0)


class TestASecondCamera:
	"""Two cameras watching the same path, reporting within a second."""

	def test_is_ignored_while_the_first_has_just_arrived(self, policy):
		policy.report(A, 100.0)
		assert policy.report(B, 101.0, on_screen=A) == IGNORE
		assert policy.camera == A

	def test_wins_once_the_dwell_has_passed(self, policy):
		policy.report(A, 100.0)
		assert policy.report(B, 106.0, on_screen=A) == SHOW
		assert policy.camera == B

	def test_wins_well_before_the_hold_ends(self, policy):
		# The hold is about rotation, not about other cameras. Twenty
		# seconds of ignoring the second camera would be useless.
		policy.report(A, 100.0)
		assert policy.report(B, 110.0, on_screen=A) == SHOW

	def test_gets_its_own_dwell(self, policy):
		policy.report(A, 100.0)
		policy.report(B, 106.0, on_screen=A)
		assert policy.report(A, 107.0, on_screen=B) == IGNORE
		assert policy.report(A, 112.0, on_screen=B) == SHOW

	def test_is_not_held_off_for_ever_by_the_first(self, policy):
		"""The reason the dwell does not restart on a repeat report.

		Somebody moving in front of A keeps A reporting. If each of those
		renewed A's dwell, B could never get the screen -- and B is the
		camera that just saw something new.
		"""
		policy.report(A, 100.0)
		for tick in range(1, 6):
			policy.report(A, 100.0 + tick, on_screen=A)
		assert policy.report(B, 106.0, on_screen=A) == SHOW


class TestTheHold:
	def test_runs_from_the_last_report(self, policy):
		policy.report(A, 100.0)
		assert policy.holding(119.0)
		assert not policy.holding(120.0)

	def test_is_not_held_before_anything_is_reported(self, policy):
		assert not policy.holding(100.0)

	def test_release_is_true_once_and_then_false(self, policy):
		policy.report(A, 100.0)
		assert not policy.release(119.0)
		assert policy.release(120.0)
		assert not policy.release(121.0)

	def test_release_forgets_the_camera(self, policy):
		policy.report(A, 100.0)
		policy.release(120.0)
		assert policy.camera is None

	def test_a_new_report_after_release_shows_again(self, policy):
		"""Rotation may have moved on, so the camera has to be replayed."""
		policy.report(A, 100.0)
		policy.release(120.0)
		assert policy.report(A, 130.0) == SHOW

	def test_release_does_nothing_when_nothing_was_reported(self, policy):
		assert not policy.release(100.0)


class TestForget:
	def test_drops_the_hold(self, policy):
		policy.report(A, 100.0)
		policy.forget()
		assert policy.camera is None
		assert not policy.holding(101.0)

	def test_lets_the_next_corner_start_clean(self, policy):
		"""A camera remembered across a closed corner must not suppress."""
		policy.report(A, 100.0)
		policy.forget()
		assert policy.report(B, 101.0) == SHOW


class TestTheHoldSetting:
	def test_can_be_changed_while_running(self, policy):
		policy.set_hold(5.0)
		policy.report(A, 100.0)
		assert not policy.holding(105.0)

	def test_clamps_the_dwell_so_it_cannot_outlast_the_hold(self):
		# A hold shorter than the dwell would protect a camera from other
		# cameras for longer than rotation is willing to leave it alone.
		policy = MotionPolicy(hold=3.0, min_dwell=6.0)
		assert policy.min_dwell == 3.0
		policy.report(A, 100.0)
		assert policy.report(B, 103.0, on_screen=A) == SHOW

	def test_the_default_dwell_is_used_when_the_hold_is_longer(self):
		policy = MotionPolicy(hold=20.0)
		assert policy.min_dwell == MIN_DWELL


class TestNoClockOfItsOwn:
	def test_the_same_report_twice_at_the_same_instant_shows_once(self, policy):
		assert policy.report(A, 100.0) == SHOW
		assert policy.report(A, 100.0, on_screen=A) == EXTEND

	def test_works_with_integer_times(self, policy):
		assert policy.report(A, 100) == SHOW
		assert policy.report(B, 101, on_screen=A) == IGNORE
		assert policy.report(B, 106, on_screen=A) == SHOW
