# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rotation as a channel in the bouquet.

The three things that make this awkward are all decisions rather than
mechanism, so all three are tested here with no receiver and no waiting:
telling our own zap from the viewer's, noticing that the viewer has left,
and knowing when the next camera is due.
"""

import pytest

from UnifiProtectDisplay.rotation import Rotation
from UnifiProtectDisplay.zaprotation import ZapRotation

ENTRY = "1:0:1:0:0:0:0:0:0:0:http%3a//127.0.0.1%3a8098/rotate:Rotation"


def reference_for(camera_id):
	return "ref:" + camera_id


@pytest.fixture
def rotation():
	return Rotation(["a", "b", "c"], enabled=True)


@pytest.fixture
def zapper(rotation):
	return ZapRotation(rotation, reference_for, entry_reference=ENTRY,
		interval=10.0)


class TestStarting:
	def test_selecting_the_entry_starts_on_a_camera(self, zapper):
		assert zapper.observe(ENTRY, now=100.0) == "ref:a"
		assert zapper.running

	def test_nothing_happens_before_the_entry_is_selected(self, zapper):
		assert zapper.observe("ref:something-else", now=100.0) is None
		assert not zapper.running

	def test_the_first_camera_is_due_one_interval_later(self, zapper):
		zapper.observe(ENTRY, now=100.0)
		assert zapper.due_at() == pytest.approx(110.0)

	def test_an_empty_selection_starts_nothing(self):
		zapper = ZapRotation(Rotation([]), reference_for, entry_reference=ENTRY)
		assert zapper.observe(ENTRY, now=0.0) is None
		assert not zapper.running

	def test_it_does_not_open_on_an_offline_camera(self):
		# Rotation that begins on an OFFLINE card looks broken from the
		# first second, and the viewer has no reason to wait and see.
		rotation = Rotation(["a", "b", "c"], is_usable=lambda cid: cid != "a")
		zapper = ZapRotation(rotation, reference_for, entry_reference=ENTRY)
		assert zapper.observe(ENTRY, now=0.0) == "ref:b"

	def test_it_still_starts_when_every_camera_is_down(self):
		# A stale offline flag on everything must not make the channel do
		# nothing at all; showing a card beats showing a dead channel.
		rotation = Rotation(["a", "b"], is_usable=lambda cid: False)
		zapper = ZapRotation(rotation, reference_for, entry_reference=ENTRY)
		assert zapper.observe(ENTRY, now=0.0) is not None
		assert zapper.running


class TestOurOwnZaps:
	def test_the_zap_we_asked_for_is_not_mistaken_for_the_viewer(self, zapper):
		zapper.observe(ENTRY, now=100.0)
		assert zapper.observe("ref:a", now=100.5) is None
		assert zapper.running

	def test_our_own_zap_does_not_restart_the_clock(self, zapper):
		zapper.observe(ENTRY, now=100.0)
		zapper.observe("ref:a", now=104.0)
		assert zapper.due_at() == pytest.approx(110.0)


class TestLeaving:
	def test_zapping_elsewhere_stops_rotation(self, zapper):
		zapper.observe(ENTRY, now=100.0)
		assert zapper.observe("1:0:1:6dca:...:BBC One", now=103.0) is None
		assert not zapper.running

	def test_a_stopped_rotation_does_nothing_on_a_tick(self, zapper):
		zapper.observe(ENTRY, now=100.0)
		zapper.observe("ref:elsewhere", now=103.0)
		assert zapper.tick(now=200.0) is None

	def test_selecting_a_camera_channel_by_hand_stops_it(self, zapper):
		# Paging onto one camera on purpose means looking at that camera,
		# not being moved off it ten seconds later.
		zapper.observe(ENTRY, now=100.0)
		assert zapper.observe("ref:c", now=102.0) is None
		assert not zapper.running

	def test_it_can_be_started_again_afterwards(self, zapper):
		zapper.observe(ENTRY, now=100.0)
		zapper.observe("ref:elsewhere", now=103.0)
		assert zapper.observe(ENTRY, now=200.0) is not None
		assert zapper.running


class TestMovingOn:
	def test_nothing_moves_before_the_interval_is_up(self, zapper):
		zapper.observe(ENTRY, now=100.0)
		assert zapper.tick(now=109.9) is None

	def test_the_next_camera_comes_up_when_it_is_due(self, zapper):
		zapper.observe(ENTRY, now=100.0)
		assert zapper.tick(now=110.0) == "ref:b"
		assert zapper.tick(now=120.0) == "ref:c"

	def test_it_wraps_round(self, zapper):
		zapper.observe(ENTRY, now=100.0)
		zapper.tick(now=110.0)
		zapper.tick(now=120.0)
		assert zapper.tick(now=130.0) == "ref:a"

	def test_a_late_tick_does_not_fire_twice(self, zapper):
		# Timers on a loaded box drift. Catching up by zapping twice would
		# put a camera on screen for no time at all.
		zapper.observe(ENTRY, now=100.0)
		assert zapper.tick(now=145.0) == "ref:b"
		assert zapper.tick(now=145.1) is None

	def test_offline_cameras_are_stepped_over(self):
		rotation = Rotation(["a", "b", "c"], is_usable=lambda cid: cid != "b")
		zapper = ZapRotation(rotation, reference_for, entry_reference=ENTRY,
			interval=10.0)
		zapper.observe(ENTRY, now=0.0)
		assert zapper.tick(now=10.0) == "ref:c"

	def test_one_camera_is_never_re_zapped(self):
		# Re-zapping the channel that is already playing blanks the
		# picture for as long as the stream takes to open, to arrive back
		# where it started.
		zapper = ZapRotation(Rotation(["a"]), reference_for,
			entry_reference=ENTRY, interval=10.0)
		assert zapper.observe(ENTRY, now=0.0) == "ref:a"
		assert zapper.tick(now=10.0) is None
		assert zapper.running

	def test_the_clock_still_advances_when_nothing_moves(self):
		zapper = ZapRotation(Rotation(["a"]), reference_for,
			entry_reference=ENTRY, interval=10.0)
		zapper.observe(ENTRY, now=0.0)
		zapper.tick(now=10.0)
		assert zapper.due_at() == pytest.approx(20.0)

	def test_a_camera_without_a_reference_is_skipped_not_fatal(self):
		def patchy(camera_id):
			if camera_id == "b":
				raise KeyError(camera_id)
			return "ref:" + camera_id

		zapper = ZapRotation(Rotation(["a", "b"]), patchy,
			entry_reference=ENTRY, interval=10.0)
		zapper.observe(ENTRY, now=0.0)
		assert zapper.tick(now=10.0) is None
		assert zapper.running
