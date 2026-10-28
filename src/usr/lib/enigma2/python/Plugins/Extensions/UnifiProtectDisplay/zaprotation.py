# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rotation driven by zapping, for the entry in the channel list.

What this is for
----------------
The bouquet has one channel per camera. Rotation is the entry that plays
them in turn without anybody pressing anything -- put it on and leave it.

Why it works by zapping
-----------------------
A channel is one service, and a service is one stream. There is no way to
make a single service be four cameras in sequence and still be live video,
so the receiver is asked to change channel on a timer instead. The pictures
are then exactly the pictures of the camera channels, decoded by the same
decoder, with no quality lost to a second encode.

The cost is honest and worth stating: every change is a service restart, so
each camera arrives after however long that camera's stream takes to open.
Nothing here can make that faster. What it can do is not make it worse --
never re-zap a channel that is already playing, and never step onto a camera
the console has said is down.

Why the logic is here and not in the screen
-------------------------------------------
Three things make this awkward, and all three are decisions rather than
mechanism: telling our own zap from the viewer's, noticing that the viewer
has left, and knowing when the next change is due. Kept here they need no
receiver and no waiting in real time to test. The glue -- the timer and the
call that actually changes channel -- is the only part that touches
enigma2, and it holds no state of its own.
"""


class ZapRotation(object):
	"""Decides what the rotation channel should be playing.

	Told what the receiver is now playing and what time it is; answers with
	a service reference to zap to, or ``None`` for "leave it alone". It
	owns no clock and starts no timers.

	``rotation`` is a :class:`~rotation.Rotation` cursor over camera ids,
	so stepping over offline cameras is the behaviour already tested there
	rather than a second copy of it. ``reference_for`` maps a camera id to
	the service reference of its channel.
	"""

	def __init__(self, rotation, reference_for, entry_reference=None,
			interval=15.0):
		self._rotation = rotation
		self._reference_for = reference_for
		self.entry_reference = entry_reference or ""
		self.interval = float(interval)
		# The reference we last asked for. Doubles as the running flag:
		# there is no state to be in where we are rotating and do not know
		# what we put on.
		self._expected = None
		self._due = 0.0

	# -- state ------------------------------------------------------------

	@property
	def running(self):
		return self._expected is not None

	@property
	def expected(self):
		"""The reference this last asked for, or None."""
		return self._expected

	def stop(self):
		self._expected = None
		self._due = 0.0

	def due_at(self):
		"""When the next change falls due. Meaningless while stopped."""
		return self._due

	# -- reacting ---------------------------------------------------------

	def observe(self, reference, now):
		"""React to the receiver having started ``reference``.

		Returns a reference to zap to, or ``None``.
		"""
		reference = reference or ""
		if self.entry_reference and reference == self.entry_reference:
			# Somebody selected the rotation channel.
			return self._begin(now)
		if not self.running:
			return None
		if reference == self._expected:
			# Our own zap coming back round. Expected, and not a reason to
			# reset the clock: the viewer did not ask for anything.
			return None
		# Anything else is the viewer changing channel, which is how you
		# leave rotation -- the same way you leave any other channel on the
		# box, with no button to learn.
		self.stop()
		return None

	def tick(self, now):
		"""Move on if the current camera's turn is up.

		Returns a reference to zap to, or ``None``. Safe to call as often
		as a timer likes.
		"""
		if not self.running or now < self._due:
			return None
		self._due = now + self.interval
		reference = self._reference_for_camera(self._rotation.next())
		if not reference or reference == self._expected:
			# One camera selected, or nowhere else to go. Re-zapping the
			# channel that is already playing would blank the picture for
			# as long as the stream takes to open, to arrive back where it
			# started.
			return None
		self._expected = reference
		return reference

	# -- internals --------------------------------------------------------

	def _begin(self, now):
		camera_id = self._rotation.current()
		if camera_id is None:
			return None
		if camera_id not in self._rotation.usable_ids():
			# Starting on a camera the console reports as down would open
			# rotation on an offline card. Step to one that is up, if
			# there is one; the cursor falls back to plain cycling when
			# there is not.
			camera_id = self._rotation.next()
		reference = self._reference_for_camera(camera_id)
		if not reference:
			return None
		self._expected = reference
		self._due = now + self.interval
		return reference

	def _reference_for_camera(self, camera_id):
		if camera_id is None:
			return None
		try:
			return self._reference_for(camera_id) or None
		except Exception:  # noqa: BLE001
			# A missing reference means one channel is skipped, which is
			# a great deal better than rotation dying on the sofa.
			return None
