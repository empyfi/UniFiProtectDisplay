# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which cameras the user picked, and what they are called.

Kept in a JSON file beside enigma2's other settings rather than in
``config.*`` for two reasons: the list is variable-length, which the config
system models badly, and the overlay has to render a camera name at startup
before any API call has returned. Caching the names makes the first frame
immediate and keeps the setup screen usable while the console is unreachable.

The file holds no credentials -- ids and names only -- so it needs no special
handling beyond being written atomically.
"""

import json
import os

DEFAULT_PATH = "/etc/enigma2/unifiprotectdisplay_cameras.json"


class CameraStore(object):
	"""Selection and name cache, persisted as JSON.

	``path``, ``reader`` and ``writer`` are injectable so the whole class can
	be tested without touching a real filesystem.
	"""

	def __init__(self, path=DEFAULT_PATH, reader=None, writer=None, log=None):
		self.path = path
		self._reader = reader or _read_file
		self._writer = writer or _write_file_atomic
		self._log = log
		self._entries = []  # ordered list of dicts: id, name, enabled

	# -- loading and saving -----------------------------------------------

	def load(self):
		"""Read the file. A missing or corrupt file yields an empty store.

		Never raises: a damaged settings file must leave the user with a
		working plugin they can reconfigure, not a plugin that will not start.
		"""
		raw = self._reader(self.path)
		if not raw:
			self._entries = []
			return self
		try:
			data = json.loads(raw)
		except ValueError as exc:
			self._warn("ignoring unreadable camera list (%s)", exc)
			self._entries = []
			return self
		self._entries = []
		if isinstance(data, list):
			for item in data:
				entry = _normalise(item)
				if entry is not None:
					self._entries.append(entry)
		return self

	def save(self):
		"""Write the file atomically. Returns True on success."""
		try:
			self._writer(self.path, json.dumps(self._entries, indent=1))
			return True
		except (OSError, IOError) as exc:
			self._warn("could not save the camera list: %s", exc)
			return False

	# -- reading ----------------------------------------------------------

	def all(self):
		"""Every known camera, in display order, as ``(id, name, enabled)``."""
		return [(e["id"], e["name"], e["enabled"]) for e in self._entries]

	def enabled_ids(self):
		"""Ids the user selected, in order. This is what rotation iterates."""
		return [e["id"] for e in self._entries if e["enabled"]]

	def name_for(self, camera_id, default=None):
		for entry in self._entries:
			if entry["id"] == camera_id:
				return entry["name"]
		return default if default is not None else camera_id

	def is_enabled(self, camera_id):
		return camera_id in self.enabled_ids()

	def __len__(self):
		return len(self._entries)

	# -- writing ----------------------------------------------------------

	def set_enabled(self, camera_id, enabled):
		for entry in self._entries:
			if entry["id"] == camera_id:
				entry["enabled"] = bool(enabled)
				return True
		return False

	def toggle(self, camera_id):
		for entry in self._entries:
			if entry["id"] == camera_id:
				entry["enabled"] = not entry["enabled"]
				return entry["enabled"]
		return None

	def merge(self, cameras):
		"""Reconcile with a fresh list from the console.

		The rules exist to keep a refresh from quietly changing what the user
		sees:

		* a camera already known keeps its enabled flag, so refreshing never
		  turns the user's selection off;
		* its name is updated, so renaming in Protect shows up here;
		* a camera that disappeared is dropped, since it can no longer play;
		* a genuinely new camera arrives disabled -- adding a camera in
		  Protect should not silently put it on the user's screen.

		Returns ``(added, removed)`` counts for the UI to report.
		"""
		incoming = [c for c in cameras if getattr(c, "id", None)]
		known = {e["id"]: e for e in self._entries}
		incoming_ids = set()
		merged = []
		added = 0
		for camera in incoming:
			incoming_ids.add(camera.id)
			entry = known.get(camera.id)
			if entry is None:
				merged.append({
					"id": camera.id,
					"name": camera.name or camera.id,
					"enabled": False,
				})
				added += 1
			else:
				entry["name"] = camera.name or entry["name"]
				merged.append(entry)
		removed = len([e for e in self._entries if e["id"] not in incoming_ids])
		self._entries = merged
		return added, removed

	def move(self, camera_id, offset):
		"""Reorder a camera. Order is the rotation order, so it is visible."""
		index = next(
			(i for i, e in enumerate(self._entries) if e["id"] == camera_id), None)
		if index is None:
			return False
		target = max(0, min(len(self._entries) - 1, index + offset))
		if target == index:
			return False
		entry = self._entries.pop(index)
		self._entries.insert(target, entry)
		return True

	def _warn(self, message, *args):
		if self._log is not None:
			self._log.error(message, *args)


def _normalise(item):
	"""Coerce one stored entry, or return None if it is unusable."""
	if not isinstance(item, dict):
		return None
	camera_id = item.get("id")
	if not camera_id:
		return None
	return {
		"id": str(camera_id),
		"name": str(item.get("name") or camera_id),
		"enabled": bool(item.get("enabled", False)),
	}


def _read_file(path):
	try:
		with open(path, "r") as handle:
			return handle.read()
	except (OSError, IOError):
		return ""


def _write_file_atomic(path, text):
	"""Write via a temporary file and rename.

	Receivers lose power without warning. A half-written settings file would
	be worse than a stale one, and rename is atomic within a filesystem.
	"""
	directory = os.path.dirname(path) or "."
	temp = os.path.join(directory, "." + os.path.basename(path) + ".tmp")
	with open(temp, "w") as handle:
		handle.write(text)
		handle.flush()
		try:
			os.fsync(handle.fileno())
		except (OSError, AttributeError):
			# Not every filesystem on these boxes supports fsync; the rename
			# is still the important half.
			pass
	os.replace(temp, path)
