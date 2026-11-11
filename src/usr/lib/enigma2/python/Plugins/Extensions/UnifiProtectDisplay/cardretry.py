# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""How often the same channel may be reopened by itself.

A channel playing an OFFLINE card watches for its camera coming back and
asks for the channel to be opened again, so that somebody who zapped there
and waited gets the picture rather than having to know to press a button.

That is a thing the plugin does to the television without being asked, so
it needs a limit. A camera on a bad connection can go up and down every few
seconds, and a channel that restarts every few seconds is worse than one
showing a card that is honestly out of date -- each restart is a black
screen for as long as the stream takes to open.

So: once per camera per interval, and nothing is remembered that has not
been used, which also means a camera that settles down leaves no trace.
"""


class RetryGate(object):
	"""Lets a key through at most once per ``interval``.

	No clock of its own: the caller says what time it is, so the awkward
	timings are tests rather than waiting.
	"""

	def __init__(self, interval=20.0):
		self.interval = float(interval)
		self._last = {}

	def allow(self, key, now):
		"""True when ``key`` may go ahead, and then not again for a while."""
		if not key:
			return False
		previous = self._last.get(key)
		if previous is not None and now - previous < self.interval:
			return False
		self._last[key] = now
		return True

	def forget(self, key=None):
		"""Drop what is remembered, for one key or for all of them."""
		if key is None:
			self._last.clear()
		else:
			self._last.pop(key, None)

	def next_allowed(self, key):
		"""When ``key`` may go again, or None if it never went."""
		previous = self._last.get(key)
		return None if previous is None else previous + self.interval
