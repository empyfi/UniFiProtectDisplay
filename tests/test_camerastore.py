# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Camera selection and name cache."""

import json

from UnifiProtectDisplay.camerastore import CameraStore
from UnifiProtectDisplay.protect.models import Camera

class FakeFile:
	"""One in-memory file, so the store can be exercised with no disk."""

	def __init__(self, content=""):
		self.content = content
		self.writes = 0
		self.fail = None

	def read(self, path):
		return self.content

	def write(self, path, text):
		if self.fail:
			raise self.fail
		self.content = text
		self.writes += 1

def store_with(entries=None, **kwargs):
	payload = json.dumps(entries) if entries is not None else ""
	backing = FakeFile(payload)
	store = CameraStore(
		path="/tmp/x.json", reader=backing.read, writer=backing.write, **kwargs)
	store.load()
	return store, backing

class TestLoading:
	def test_missing_file_gives_an_empty_store(self):
		store, _ = store_with()
		assert len(store) == 0
		assert store.enabled_ids() == []

	def test_reads_entries(self):
		store, _ = store_with([
			{"id": "1", "name": "Hof", "enabled": True},
			{"id": "2", "name": "Tor", "enabled": False},
		])
		assert store.all() == [("1", "Hof", True), ("2", "Tor", False)]
		assert store.enabled_ids() == ["1"]

	def test_corrupt_json_does_not_raise(self):
		# A damaged settings file must leave a working plugin behind, not
		# one that refuses to start.
		backing = FakeFile("{not json")
		store = CameraStore(reader=backing.read, writer=backing.write)
		store.load()
		assert len(store) == 0

	def test_entries_without_an_id_are_dropped(self):
		store, _ = store_with([{"name": "nameless"}, {"id": "2"}])
		assert [c[0] for c in store.all()] == ["2"]

	def test_name_defaults_to_the_id(self):
		store, _ = store_with([{"id": "abc"}])
		assert store.name_for("abc") == "abc"

	def test_non_list_payload_is_ignored(self):
		backing = FakeFile('{"id": "1"}')
		store = CameraStore(reader=backing.read, writer=backing.write)
		store.load()
		assert len(store) == 0

class TestSaving:
	def test_save_writes_json(self):
		store, backing = store_with([{"id": "1", "name": "Hof", "enabled": True}])
		assert store.save() is True
		assert json.loads(backing.content)[0]["name"] == "Hof"

	def test_save_failure_is_reported_not_raised(self):
		# A read-only /etc must not crash the setup screen.
		logged = []

		class Log:
			def error(self, message, *args):
				logged.append(message % args if args else message)

		store, backing = store_with([{"id": "1"}], log=Log())
		backing.fail = IOError("read-only filesystem")
		assert store.save() is False
		assert logged

class TestSelection:
	def test_toggle_flips_and_returns_the_new_state(self):
		store, _ = store_with([{"id": "1", "enabled": False}])
		assert store.toggle("1") is True
		assert store.toggle("1") is False

	def test_toggle_unknown_camera_returns_none(self):
		store, _ = store_with([{"id": "1"}])
		assert store.toggle("zzz") is None

	def test_set_enabled(self):
		store, _ = store_with([{"id": "1", "enabled": False}])
		assert store.set_enabled("1", True) is True
		assert store.is_enabled("1")

	def test_enabled_ids_keeps_file_order(self):
		# Order is the rotation order, so it is user-visible.
		store, _ = store_with([
			{"id": "c", "enabled": True},
			{"id": "a", "enabled": True},
			{"id": "b", "enabled": False},
		])
		assert store.enabled_ids() == ["c", "a"]

class TestMerge:
	def test_new_cameras_arrive_disabled(self):
		# Adding a camera in Protect should not silently put it on the
		# user's television.
		store, _ = store_with([])
		added, removed = store.merge([Camera("1", "Hof"), Camera("2", "Tor")])
		assert (added, removed) == (2, 0)
		assert store.enabled_ids() == []

	def test_refresh_keeps_the_existing_selection(self):
		store, _ = store_with([{"id": "1", "name": "Hof", "enabled": True}])
		store.merge([Camera("1", "Hof")])
		assert store.enabled_ids() == ["1"]

	def test_rename_in_protect_is_picked_up(self):
		store, _ = store_with([{"id": "1", "name": "Old", "enabled": True}])
		store.merge([Camera("1", "New name")])
		assert store.name_for("1") == "New name"
		assert store.is_enabled("1")

	def test_removed_camera_is_dropped(self):
		store, _ = store_with([
			{"id": "1", "enabled": True}, {"id": "2", "enabled": True}])
		added, removed = store.merge([Camera("1", "Hof")])
		assert (added, removed) == (0, 1)
		assert [c[0] for c in store.all()] == ["1"]

	def test_merge_follows_console_order(self):
		store, _ = store_with([{"id": "2"}, {"id": "1"}])
		store.merge([Camera("1", "A"), Camera("2", "B")])
		assert [c[0] for c in store.all()] == ["1", "2"]

	def test_cameras_without_an_id_are_ignored(self):
		store, _ = store_with([])
		added, _ = store.merge([Camera("", "broken"), Camera("1", "ok")])
		assert added == 1

class TestOrdering:
	def test_move_down(self):
		store, _ = store_with([{"id": "a"}, {"id": "b"}, {"id": "c"}])
		assert store.move("a", 1) is True
		assert [c[0] for c in store.all()] == ["b", "a", "c"]

	def test_move_up(self):
		store, _ = store_with([{"id": "a"}, {"id": "b"}])
		assert store.move("b", -1) is True
		assert [c[0] for c in store.all()] == ["b", "a"]

	def test_move_past_the_end_is_a_noop(self):
		store, _ = store_with([{"id": "a"}, {"id": "b"}])
		assert store.move("b", 1) is False
		assert [c[0] for c in store.all()] == ["a", "b"]

	def test_move_unknown_camera(self):
		store, _ = store_with([{"id": "a"}])
		assert store.move("zzz", 1) is False
