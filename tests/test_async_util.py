# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Off-main-loop work and its delivery back onto the main loop.

The threads here are real, but delivery is driven by calling ``drain()``
explicitly rather than by a timer, which is exactly how enigma2 drives it --
just deterministically.
"""

import threading

from UnifiProtectDisplay.async_util import TaskRunner


class FakeTimer:
	"""Stand-in for an enigma2 eTimer."""

	def __init__(self):
		self.callback = []
		self.started = 0
		self.stopped = 0
		self.running = False

	def start(self, interval_ms, single_shot):
		self.started += 1
		self.running = True

	def stop(self):
		self.stopped += 1
		self.running = False


def runner_with_timer():
	timer_holder = {}

	def factory():
		timer_holder["timer"] = FakeTimer()
		return timer_holder["timer"]

	runner = TaskRunner(timer_factory=factory)
	return runner, timer_holder["timer"]


def drain_after_completion(runner, expected=1, timeout=5.0):
	"""Wait for ``expected`` results to land, then deliver on this thread.

	``expected=0`` drains straight away -- used where the point is that
	nothing was ever submitted, so waiting would only burn the timeout.
	"""
	if expected:
		waiter = threading.Event()
		deadline = timeout / 0.01
		attempts = 0
		while runner._queue.qsize() < expected and attempts < deadline:
			waiter.wait(0.01)
			attempts += 1
	runner.drain()


class TestSuccessPath:
	def test_result_is_delivered(self):
		runner, _ = runner_with_timer()
		got = []
		runner.run(lambda: 42, on_success=got.append)
		drain_after_completion(runner)
		assert got == [42]

	def test_work_runs_off_the_calling_thread(self):
		# The whole point: an HTTP call must not block enigma2's main loop.
		runner, _ = runner_with_timer()
		seen = []
		runner.run(lambda: threading.current_thread().ident,
			on_success=seen.append)
		drain_after_completion(runner)
		assert seen and seen[0] != threading.current_thread().ident

	def test_callback_runs_on_the_draining_thread(self):
		# Callbacks touch GUI objects, so they must arrive on the main loop.
		runner, _ = runner_with_timer()
		seen = []
		runner.run(lambda: None, on_success=lambda _: seen.append(
			threading.current_thread().ident))
		drain_after_completion(runner)
		assert seen == [threading.current_thread().ident]


class TestErrorPath:
	def test_exception_goes_to_the_error_handler(self):
		runner, _ = runner_with_timer()
		failures = []

		def boom():
			raise ValueError("nope")

		runner.run(boom, on_error=failures.append)
		drain_after_completion(runner)
		assert len(failures) == 1
		assert isinstance(failures[0], ValueError)

	def test_success_handler_is_not_called_on_failure(self):
		runner, _ = runner_with_timer()
		good = []
		runner.run(lambda: 1 / 0, on_success=good.append, on_error=lambda e: None)
		drain_after_completion(runner)
		assert good == []

	def test_a_raising_callback_does_not_stop_other_deliveries(self):
		# An exception escaping into enigma2's main loop shows the user a
		# crash screen; it must be contained here.
		runner, _ = runner_with_timer()
		delivered = []

		def bad(_):
			raise RuntimeError("callback is broken")

		runner.run(lambda: "a", on_success=bad)
		runner.run(lambda: "b", on_success=delivered.append)
		drain_after_completion(runner, expected=2)
		assert delivered == ["b"]

	def test_failure_without_a_handler_is_logged_not_raised(self):
		logged = []

		class Log:
			def error(self, message, *args):
				logged.append(message % args if args else message)

		runner = TaskRunner(timer_factory=FakeTimer, log=Log())
		runner.run(lambda: 1 / 0, name="divide")
		drain_after_completion(runner)
		assert any("divide" in line for line in logged)


class TestCancellation:
	def test_cancelled_task_delivers_nothing(self):
		# A snapshot arriving after its overlay closed must not be painted
		# into a destroyed widget.
		runner, _ = runner_with_timer()
		got = []
		task = runner.run(lambda: "late", on_success=got.append)
		task.cancel()
		drain_after_completion(runner)
		assert got == []
		assert task.cancelled

	def test_shutdown_cancels_everything_outstanding(self):
		runner, _ = runner_with_timer()
		got = []
		runner.run(lambda: "a", on_success=got.append)
		runner.shutdown()
		drain_after_completion(runner)
		assert got == []

	def test_submitting_after_shutdown_is_accepted_and_dropped(self):
		# Teardown order varies; callers should not need to guard every call.
		runner, _ = runner_with_timer()
		runner.shutdown()
		got = []
		task = runner.run(lambda: "x", on_success=got.append)
		assert task.cancelled
		drain_after_completion(runner, expected=0)
		assert got == []


class TestTimerManagement:
	def test_timer_starts_when_work_is_submitted(self):
		runner, timer = runner_with_timer()
		runner.run(lambda: None)
		assert timer.started >= 1

	def test_timer_stops_once_the_queue_is_empty(self):
		# The poll timer should not tick while there is nothing to poll for.
		runner, timer = runner_with_timer()
		runner.run(lambda: None)
		drain_after_completion(runner)
		assert timer.stopped >= 1
		assert not timer.running

	def test_shutdown_stops_the_timer(self):
		runner, timer = runner_with_timer()
		runner.run(lambda: None)
		runner.shutdown()
		assert not timer.running

	def test_works_without_a_timer_at_all(self):
		# Useful for one-shot use from code that drains by hand.
		runner = TaskRunner()
		got = []
		runner.run(lambda: 7, on_success=got.append)
		drain_after_completion(runner)
		assert got == [7]
