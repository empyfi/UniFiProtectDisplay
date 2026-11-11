# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""How often a channel may reopen itself.

A channel playing an OFFLINE card reopens when its camera comes back, which
is the plugin changing channel without being asked. A camera on a bad
connection can go up and down every few seconds, and a channel restarting
every few seconds is worse than a card that is honestly out of date --
every restart is a black screen for as long as a stream takes to open.
"""

from UnifiProtectDisplay.cardretry import RetryGate

A = "cam-a"
B = "cam-b"


class TestFirstTime:
	def test_goes_through(self):
		gate = RetryGate(interval=20.0)
		assert gate.allow(A, 100.0)

	def test_a_camera_without_an_id_never_goes(self):
		gate = RetryGate()
		assert not gate.allow(None, 100.0)
		assert not gate.allow("", 100.0)


class TestTheInterval:
	def test_a_second_try_too_soon_is_refused(self):
		gate = RetryGate(interval=20.0)
		gate.allow(A, 100.0)
		assert not gate.allow(A, 110.0)

	def test_a_second_try_after_the_interval_goes(self):
		gate = RetryGate(interval=20.0)
		gate.allow(A, 100.0)
		assert gate.allow(A, 120.0)

	def test_a_refused_try_does_not_push_the_interval_out(self):
		# Otherwise a camera flapping every second could never come back
		# at all: each refusal would move the gate.
		gate = RetryGate(interval=20.0)
		gate.allow(A, 100.0)
		for tick in range(1, 20):
			gate.allow(A, 100.0 + tick)
		assert gate.allow(A, 120.0)

	def test_a_flapping_camera_goes_at_most_once_per_interval(self):
		gate = RetryGate(interval=20.0)
		went = [t for t in range(100, 200) if gate.allow(A, float(t))]
		assert went == [100, 120, 140, 160, 180]


class TestPerCamera:
	def test_cameras_do_not_hold_each_other_back(self):
		gate = RetryGate(interval=20.0)
		assert gate.allow(A, 100.0)
		assert gate.allow(B, 100.0)

	def test_each_keeps_its_own_clock(self):
		gate = RetryGate(interval=20.0)
		gate.allow(A, 100.0)
		gate.allow(B, 110.0)
		assert not gate.allow(A, 119.0)
		assert gate.allow(A, 120.0)
		assert not gate.allow(B, 125.0)
		assert gate.allow(B, 130.0)


class TestForgetting:
	def test_one_camera_can_be_forgotten(self):
		gate = RetryGate(interval=20.0)
		gate.allow(A, 100.0)
		gate.forget(A)
		assert gate.allow(A, 101.0)

	def test_everything_can_be_forgotten(self):
		# What stopping the bouquet proxy does: nothing is playing any
		# more, so nothing is owed a wait.
		gate = RetryGate(interval=20.0)
		gate.allow(A, 100.0)
		gate.allow(B, 100.0)
		gate.forget()
		assert gate.allow(A, 101.0)
		assert gate.allow(B, 101.0)

	def test_forgetting_something_unknown_is_harmless(self):
		gate = RetryGate()
		gate.forget("never-seen")


class TestNextAllowed:
	def test_is_none_before_the_first_try(self):
		assert RetryGate().next_allowed(A) is None

	def test_is_the_interval_after_the_last_try(self):
		gate = RetryGate(interval=20.0)
		gate.allow(A, 100.0)
		assert gate.next_allowed(A) == 120.0
