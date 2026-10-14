# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which camera is on screen, and when it changes.

Pure state, no timers and no enigma2: the three display modes (snapshot
overlay, fullscreen, remux PiP) all rotate, and each drives this from its own
timer. Keeping the decision separate from the clock is what makes the
behaviour testable without waiting in real time.
"""


class Rotation(object):
	"""Cursor over the selected cameras.

	Holding ids rather than :class:`~protect.models.Camera` objects keeps the
	selection valid across a camera-list refresh: a camera that is
	temporarily unreachable keeps its slot instead of silently shifting every
	other camera's position.
	"""

	def __init__(self, camera_ids=None, enabled=True):
		self._ids = list(camera_ids or [])
		self.enabled = enabled
		self._index = 0
		self._paused = False

	# -- selection --------------------------------------------------------

	def set_cameras(self, camera_ids):
		"""Replace the list, keeping the current camera on screen if it survives.

		Without this, editing the selection in setup would yank the picture
		back to the first camera every time.
		"""
		current = self.current()
		self._ids = list(camera_ids or [])
		if current in self._ids:
			self._index = self._ids.index(current)
		else:
			self._index = 0

	def camera_ids(self):
		return list(self._ids)

	def __len__(self):
		return len(self._ids)

	def current(self):
		"""Id now on screen, or ``None`` when nothing is selected."""
		if not self._ids:
			return None
		if self._index >= len(self._ids):
			self._index = 0
		return self._ids[self._index]

	# -- movement ---------------------------------------------------------

	def advance(self):
		"""Step to the next camera and return it.

		A no-op when rotation is off, paused, or there is only one camera, so
		callers can drive this unconditionally from a timer.
		"""
		if not self._ids:
			return None
		if self.should_rotate():
			self._index = (self._index + 1) % len(self._ids)
		return self.current()

	def next(self):
		"""Step forward regardless of the rotation setting.

		This is the manual "next camera" action: the user asking for the next
		camera means it, whether or not automatic rotation is enabled.
		"""
		if not self._ids:
			return None
		self._index = (self._index + 1) % len(self._ids)
		return self.current()

	def previous(self):
		if not self._ids:
			return None
		self._index = (self._index - 1) % len(self._ids)
		return self.current()

	def select(self, camera_id):
		"""Jump to ``camera_id``. Returns False when it is not in the list."""
		if camera_id in self._ids:
			self._index = self._ids.index(camera_id)
			return True
		return False

	# -- pausing ----------------------------------------------------------

	def pause(self):
		"""Hold on the current camera.

		Used while the user is looking at one camera on purpose, so the view
		does not slide away mid-look.
		"""
		self._paused = True

	def resume(self):
		self._paused = False

	@property
	def paused(self):
		return self._paused

	def should_rotate(self):
		"""True when :meth:`advance` would actually move."""
		return self.enabled and not self._paused and len(self._ids) > 1
