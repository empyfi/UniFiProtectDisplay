# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Running blocking work off the enigma2 main loop.

The problem
-----------
Enigma2 is single-threaded: the GUI, zapping, recordings and every plugin all
run in one main loop. An HTTP call made from that loop blocks everything until
it returns, so an NVR that is powered off freezes the user's television for
the length of the timeout. That is unacceptable for a plugin whose whole job
is to poll a network device.

The shape
---------
Work runs on a short-lived daemon thread. Results come back through a queue
that a single enigma2 timer drains *on the main loop*, so callbacks touch GUI
objects from the only thread allowed to. This is the standard enigma2 pattern
and avoids pulling in twisted's reactor, whose availability varies by image.

Cancellation matters as much as dispatch: a snapshot that arrives after its
overlay has been closed must not be painted into a destroyed widget. Every
submission returns a :class:`Task` whose :meth:`Task.cancel` makes the result
be dropped, and :meth:`TaskRunner.shutdown` cancels everything at once.
"""

import threading

try:  # pragma: no cover - only importable on a receiver
	from Queue import Queue, Empty
except ImportError:  # pragma: no cover
	from queue import Queue, Empty

# How often the main loop drains finished work. 100 ms is below the threshold
# where a picture update feels delayed, and costs nothing measurable: the
# timer only runs while work is outstanding.
POLL_INTERVAL_MS = 100


class Task(object):
	"""Handle for one submitted callable."""

	__slots__ = ("_cancelled", "on_success", "on_error", "name")

	def __init__(self, on_success=None, on_error=None, name=""):
		self._cancelled = False
		self.on_success = on_success
		self.on_error = on_error
		self.name = name or "task"

	def cancel(self):
		"""Drop this task's result when it arrives.

		The worker thread is not interrupted -- Python cannot safely do that,
		and a socket read is already bounded by its timeout. What is
		guaranteed is that no callback fires after this returns.
		"""
		self._cancelled = True

	@property
	def cancelled(self):
		return self._cancelled


class TaskRunner(object):
	"""Submits callables to threads and delivers results on the main loop.

	``timer_factory`` must return an enigma2-style timer: an object with
	``start(ms, singleShot)``, ``stop()`` and a ``callback`` list. Injectable
	so tests can drive the drain by hand instead of waiting.
	"""

	def __init__(self, timer_factory=None, log=None, thread_factory=None):
		self._log = log
		self._queue = Queue()
		self._pending = 0
		self._lock = threading.Lock()
		self._tasks = []
		self._shutdown = False
		self._thread_factory = thread_factory or _default_thread
		self._timer = None
		self._timer_factory = timer_factory
		if timer_factory is not None:
			self._timer = timer_factory()
			self._timer.callback.append(self.drain)

	# -- submission -------------------------------------------------------

	def run(self, func, on_success=None, on_error=None, name=""):
		"""Call ``func()`` on a worker thread; deliver the outcome on the main loop.

		``on_success`` receives the return value, ``on_error`` the exception.
		Exactly one of them fires, unless the task is cancelled, in which case
		neither does.
		"""
		task = Task(on_success, on_error, name)
		if self._shutdown:
			# After shutdown, accept and immediately drop: callers tearing
			# down should not have to guard every call site.
			task.cancel()
			return task
		with self._lock:
			self._tasks.append(task)
			self._pending += 1
		thread = self._thread_factory(self._worker, (func, task))
		thread.start()
		self._ensure_timer()
		return task

	def _worker(self, func, task):
		"""Thread body. Never raises: an escaping exception kills the thread
		silently and the caller would wait for a result that never comes."""
		try:
			result = func()
			self._queue.put((task, True, result))
		except Exception as exc:  # noqa: BLE001 - deliberately broad, see above
			self._queue.put((task, False, exc))

	# -- delivery ---------------------------------------------------------

	def drain(self):
		"""Deliver every finished result. Runs on the main loop.

		Called by the timer; tests call it directly.
		"""
		while True:
			try:
				task, ok, payload = self._queue.get_nowait()
			except Empty:
				break
			with self._lock:
				self._pending = max(0, self._pending - 1)
				if task in self._tasks:
					self._tasks.remove(task)
			if task.cancelled:
				continue
			self._dispatch(task, ok, payload)
		if self._pending == 0:
			self._stop_timer()

	def _dispatch(self, task, ok, payload):
		callback = task.on_success if ok else task.on_error
		if callback is None:
			if not ok:
				self._warn("%s failed with no error handler: %s", task.name, payload)
			return
		try:
			callback(payload)
		except Exception as exc:  # noqa: BLE001
			# A raising callback must not stop the remaining results from
			# being delivered, and must never reach enigma2's main loop,
			# where an unhandled exception shows the user a crash screen.
			self._warn("callback for %s raised: %s", task.name, exc)

	# -- lifecycle --------------------------------------------------------

	def shutdown(self):
		"""Cancel everything outstanding and stop the timer.

		Call from a screen's teardown. Worker threads are daemons, so any
		still waiting on a socket cannot keep enigma2 from exiting.
		"""
		self._shutdown = True
		with self._lock:
			for task in self._tasks:
				task.cancel()
			self._tasks = []
			self._pending = 0
		self._stop_timer()

	def _ensure_timer(self):
		if self._timer is not None:
			self._timer.start(POLL_INTERVAL_MS, False)

	def _stop_timer(self):
		if self._timer is not None:
			self._timer.stop()

	def _warn(self, message, *args):
		if self._log is not None:
			self._log.error(message, *args)


def _default_thread(target, args):
	thread = threading.Thread(target=target, args=args)
	# Daemon: enigma2 must be able to shut down while a socket read is still
	# blocked on an unreachable console.
	thread.daemon = True
	return thread
