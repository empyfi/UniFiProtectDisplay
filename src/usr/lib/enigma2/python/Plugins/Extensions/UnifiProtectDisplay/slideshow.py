# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rotation as a channel of stills, rather than by changing channel.

The other way round from :mod:`zaprotation`
-------------------------------------------
That one asks the receiver to change channel on a timer, so the picture is
the camera's own video. This one never changes channel at all: one service
plays throughout, and what goes into it is a snapshot of whichever camera's
turn it is. The two are different trades of the same coin and both can sit
in the bouquet at once.

What it costs and what it buys
------------------------------
It buys a camera change that costs nothing -- no service restart, no black
screen, and an offline camera simply shows its card in its turn instead of
holding everything up. It costs the moving picture: roughly one new
photograph a second against twenty-five frames, and the stream runs a
second or so behind the world because of the cushion the encoder is primed
with (see :mod:`player.imagestream`).

What is decided here
--------------------
When to move to the next camera, when the picture in hand is too old to
show again, and what to show when a camera is down or the console will not
answer. All of it against a clock passed in, so none of it needs a
receiver or any real waiting to test. Fetching and drawing are handed in
as callables for the same reason.
"""


class SlideRotation(object):
	"""The picture a rotating stills channel should be showing.

	``cursor`` is a :class:`~rotation.Rotation` over camera ids -- its own,
	not the one the corner uses, or watching this channel would drag the
	corner along with it.

	``fetch(camera_id)`` returns JPEG bytes or None, ``describe(camera_id)``
	returns ``(name, connected)``, and ``render(name, data)`` turns the two
	into a frame.

	:meth:`frame` always hands back a picture. What is economised is the
	request to the console and the drawing, not the answer: most calls
	return the same bytes as the call before, having done nothing. It has
	to be that way round because the proxy asks a route for a frame to
	find out whether the route exists at all, and a viewer who has just
	connected has nothing to fall back on.
	"""

	def __init__(self, cursor, fetch, describe, render,
			interval=15.0, refresh=2.0):
		self._cursor = cursor
		self._fetch = fetch
		self._describe = describe
		self._render = render
		self.interval = float(interval)
		self.refresh = float(refresh)
		self._camera = None
		self._switch_at = None
		self._fetched_at = None
		self._frame = None

	# -- state ------------------------------------------------------------

	@property
	def camera(self):
		"""Whose turn it is, or None before the first frame."""
		return self._camera

	def reset(self):
		"""Start again from the first camera, forgetting the picture.

		Somebody coming back to this channel should find it where they
		found it last time, at the top, rather than wherever it happened
		to have got to while nobody was watching.
		"""
		ids = self._cursor.camera_ids()
		if ids:
			self._cursor.select(ids[0])
		self._camera = None
		self._switch_at = None
		self._fetched_at = None
		self._frame = None

	# -- the picture ------------------------------------------------------

	def frame(self, now):
		"""The picture to show at ``now``. Never None once there is one."""
		if not self._cursor.camera_ids():
			# Nothing selected. Say so, rather than leaving the channel
			# with no picture and no explanation.
			if self._frame is None:
				self._frame = self._render(None, None)
			return self._frame
		if self._switch_at is None or now >= self._switch_at:
			self._advance(now)
		elif self._fresh(now):
			return self._frame
		return self._take(now)

	def _advance(self, now):
		"""Move to the next camera. Offline ones keep their turn.

		Stepping over them is right for the corner, where an offline
		camera is a blank screen; here it is a card that says which
		camera is down, which is worth the same few seconds as any other.
		"""
		if self._camera is None:
			self._camera = self._cursor.current()
		else:
			self._camera = self._cursor.next()
		self._switch_at = now + self.interval
		self._fetched_at = None

	def _fresh(self, now):
		return (self._frame is not None and self._fetched_at is not None
			and now - self._fetched_at < self.refresh)

	def _take(self, now):
		"""Fetch and draw the current camera. Never raises."""
		self._fetched_at = now
		camera_id = self._camera
		try:
			name, connected = self._describe(camera_id)
		except Exception:  # noqa: BLE001
			name, connected = "", False
		data = None
		if connected:
			try:
				data = self._fetch(camera_id)
			except Exception:  # noqa: BLE001
				# A console that blinks must not blank the television.
				# Keep whatever is on screen and try again next time.
				data = None
				if self._frame is not None:
					return self._frame
		try:
			frame = self._render(name, data)
		except Exception:  # noqa: BLE001
			frame = None
		if frame is not None:
			self._frame = frame
		return self._frame
