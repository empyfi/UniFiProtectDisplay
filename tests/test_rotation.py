# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Camera rotation state."""

from UnifiProtectDisplay.rotation import Rotation


class TestEmpty:
	def test_nothing_selected(self):
		rot = Rotation()
		assert rot.current() is None
		assert rot.advance() is None
		assert rot.next() is None
		assert rot.previous() is None
		assert len(rot) == 0


class TestSingleCamera:
	def test_stays_put_on_advance(self):
		# Rotating a one-camera list would restart the stream for no reason.
		rot = Rotation(["a"])
		assert rot.current() == "a"
		assert rot.advance() == "a"
		assert not rot.should_rotate()


class TestCycling:
	def test_advance_wraps_around(self):
		rot = Rotation(["a", "b", "c"])
		assert [rot.advance() for _ in range(4)] == ["b", "c", "a", "b"]

	def test_previous_wraps_backwards(self):
		rot = Rotation(["a", "b", "c"])
		assert rot.previous() == "c"
		assert rot.previous() == "b"

	def test_select_jumps_to_a_camera(self):
		rot = Rotation(["a", "b", "c"])
		assert rot.select("c") is True
		assert rot.current() == "c"

	def test_select_rejects_unknown_camera(self):
		rot = Rotation(["a", "b"])
		assert rot.select("zzz") is False
		assert rot.current() == "a"


class TestDisabledAndPaused:
	def test_advance_is_a_noop_when_rotation_is_off(self):
		rot = Rotation(["a", "b"], enabled=False)
		assert rot.advance() == "a"

	def test_pause_holds_the_current_camera(self):
		rot = Rotation(["a", "b", "c"])
		rot.advance()
		rot.pause()
		assert rot.advance() == "b"
		assert rot.paused
		rot.resume()
		assert rot.advance() == "c"

	def test_manual_next_overrides_disabled_rotation(self):
		# Asking for the next camera means it, whatever the auto setting is.
		rot = Rotation(["a", "b"], enabled=False)
		assert rot.next() == "b"

	def test_manual_next_overrides_pause(self):
		rot = Rotation(["a", "b"])
		rot.pause()
		assert rot.next() == "b"


class TestSelectionChanges:
	def test_current_camera_survives_a_list_change(self):
		# Editing the selection in setup must not yank the picture back to
		# the first camera.
		rot = Rotation(["a", "b", "c"])
		rot.select("c")
		rot.set_cameras(["x", "c", "y"])
		assert rot.current() == "c"

	def test_falls_back_to_the_first_when_current_is_removed(self):
		rot = Rotation(["a", "b"])
		rot.select("b")
		rot.set_cameras(["x", "y"])
		assert rot.current() == "x"

	def test_empty_list_clears_the_current_camera(self):
		rot = Rotation(["a"])
		rot.set_cameras([])
		assert rot.current() is None

	def test_camera_ids_returns_a_copy(self):
		# Callers hand this to config widgets; aliasing would let the UI
		# mutate rotation state behind its back.
		rot = Rotation(["a", "b"])
		ids = rot.camera_ids()
		ids.append("c")
		assert len(rot) == 2


class TestIndexSafety:
	def test_shrinking_the_list_cannot_leave_a_stale_index(self):
		rot = Rotation(["a", "b", "c"])
		rot.select("c")
		rot._ids = ["a"]  # simulate an external shrink
		assert rot.current() == "a"


class TestSkippingUnusableCameras:
	"""Skipping is available but off by default: an offline camera now keeps
	its place and is labelled instead, so the user can see it is down."""

	@staticmethod
	def rot(ids, offline=()):
		return Rotation(ids, is_usable=lambda cid: cid not in offline)

	def test_next_steps_over_an_offline_camera(self):
		r = self.rot(["a", "b", "c"], offline={"b"})
		assert r.next() == "c"

	def test_two_cameras_one_offline_stays_on_the_working_one(self):
		# Exactly the reported case: every second press went black.
		r = self.rot(["a", "b"], offline={"b"})
		assert r.next() == "a"
		assert r.next() == "a"

	def test_previous_also_skips(self):
		r = self.rot(["a", "b", "c"], offline={"b"})
		r.select("c")
		assert r.previous() == "a"

	def test_several_offline_in_a_row_are_all_skipped(self):
		r = self.rot(["a", "b", "c", "d"], offline={"b", "c"})
		assert r.next() == "d"

	def test_all_offline_still_moves(self):
		# A stale offline flag must not make the button stop working.
		r = self.rot(["a", "b"], offline={"a", "b"})
		assert r.next() == "b"
		assert r.next() == "a"

	def test_automatic_rotation_skips_too(self):
		r = Rotation(["a", "b", "c"], enabled=True,
			is_usable=lambda cid: cid != "b")
		assert r.advance() == "c"

	def test_without_a_predicate_nothing_is_skipped(self):
		r = Rotation(["a", "b", "c"])
		assert r.next() == "b"

	def test_usable_ids_reports_the_filtered_list(self):
		r = self.rot(["a", "b", "c"], offline={"b"})
		assert r.usable_ids() == ["a", "c"]

	def test_select_still_reaches_an_offline_camera(self):
		# Skipping is for stepping; an explicit choice is the user's.
		r = self.rot(["a", "b"], offline={"b"})
		assert r.select("b") is True
		assert r.current() == "b"
