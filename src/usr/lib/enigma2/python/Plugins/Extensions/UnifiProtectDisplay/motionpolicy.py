# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a reported motion should do to the picture.

Protect reports motion as a stream of separate alarms, not as a state. One
person walking past a camera produces several, and two cameras watching the
same path produce two sets of them within a second or two. Deciding what to
put on screen is therefore a question about a *sequence* of reports, which is
why it lives here rather than in the manager: pure state, no timer and no
clock of its own, so the awkward sequences are tests instead of guesses.

Two rules, and they come from two different failures.

A camera already on screen is never put there again. Showing a camera costs
a request to Protect and a decoder restart, so acting on every alarm tore the
picture down and rebuilt it while the camera was still in view of whoever
triggered it. Repeat alarms extend how long the camera is kept, and change
nothing else.

A camera that has just arrived is left alone briefly, even if another camera
reports motion. Without that, two cameras seeing the same person swap the
screen back and forth faster than a stream can open, and the result is not
"both cameras" but neither: every swap restarts the decoder before anything
became visible. The dwell is a floor on how long a camera gets, not a
ceiling -- once it has passed, newer motion wins immediately, because the
newer camera is the more interesting one.

The hold is the older, separate idea: it keeps automatic rotation from
sliding off a camera motion has just brought up. Rotation asks about it
through :meth:`MotionPolicy.release`.
"""

# What to do with a report.
IGNORE = "ignore"
EXTEND = "extend"
SHOW = "show"

# How long a camera keeps the screen against another camera's motion.
#
# It has to outlast opening a stream, or a swap lands in the gap before the
# first frame and nothing is ever seen. Opening a live camera on the
# receiver has not been measured -- both cameras were disconnected while
# this was written -- so this is a floor chosen to be comfortably longer
# than the 1.3 s a card takes, and it is the one number here worth
# revisiting once a camera can be timed.
MIN_DWELL = 6.0

DEFAULT_HOLD = 20.0


class MotionPolicy(object):
	"""Decides, for one corner, what each motion report should do.

	``hold`` is the user's *seconds a camera stays on screen after motion*.
	``min_dwell`` is clamped to it: a hold shorter than the dwell would mean
	a camera protected from other cameras for longer than rotation is
	willing to leave it alone, which is contradictory.
	"""

	def __init__(self, hold=DEFAULT_HOLD, min_dwell=MIN_DWELL):
		self._min_dwell = float(min_dwell)
		self.set_hold(hold)
		self._camera = None
		self._shown_at = 0.0
		self._hold_until = 0.0

	def set_hold(self, seconds):
		"""Re-read the hold, which the user can change in setup."""
		self.hold = float(seconds)

	@property
	def min_dwell(self):
		return min(self._min_dwell, self.hold)

	@property
	def camera(self):
		"""The camera motion last put on screen, or ``None``."""
		return self._camera

	# -- reports ----------------------------------------------------------

	def report(self, camera_id, now, on_screen=None):
		"""Decide what to do about motion on ``camera_id``.

		``on_screen`` is the camera the corner is showing now, which is the
		only way to tell "switch to this camera" from "this camera is
		already the picture" -- the second must not restart anything.

		Returns :data:`SHOW`, :data:`EXTEND` or :data:`IGNORE`.
		"""
		if not camera_id:
			return IGNORE
		if camera_id == on_screen:
			self._adopt(camera_id, now)
			return EXTEND
		if self._camera is not None and now < self._shown_at + self.min_dwell:
			return IGNORE
		self._adopt(camera_id, now)
		return SHOW

	def _adopt(self, camera_id, now):
		if camera_id != self._camera:
			self._camera = camera_id
			# The dwell counts from when a camera reached the screen and is
			# deliberately not pushed forward by that camera's own later
			# alarms. Otherwise somebody moving about in front of one camera
			# would hold the screen against a second camera indefinitely,
			# which is exactly the moment the second camera matters.
			self._shown_at = now
		self._hold_until = now + self.hold

	# -- the hold ---------------------------------------------------------

	def holding(self, now):
		"""True while rotation should stay off the current camera."""
		return self._camera is not None and now < self._hold_until

	def release(self, now):
		"""Give up the hold once it has run out.

		True only on the call that ends it, so the caller can stop its timer
		and resume rotation exactly once.
		"""
		if self._camera is None or now < self._hold_until:
			return False
		self.forget()
		return True

	def forget(self):
		"""Drop the state: motion no longer governs what is on screen.

		Called when the corner closes. Without it a camera remembered from
		an earlier corner could suppress the first motion of the next one.
		"""
		self._camera = None
		self._shown_at = 0.0
		self._hold_until = 0.0
