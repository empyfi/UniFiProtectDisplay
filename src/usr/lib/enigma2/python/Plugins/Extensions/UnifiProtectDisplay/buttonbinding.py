# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Putting the camera corner on the box's PiP key.

What the key should do
----------------------
One key, four outcomes, which is the whole point -- the camera corner is
something you reach for while watching, not something worth a menu:

	short press, nothing showing   normal Picture-in-Picture, as before
	short press, something showing everything off
	long press,  nothing showing   camera corner on
	long press,  corner showing    next camera

How the binding works
---------------------
openATV maps keys through ``config.misc.ButtonSetup.<key>``, where a long
press is the same key name with ``_long`` appended. A plugin is named by the
directory it lives in plus an index, because several plugins can share one
directory::

	Plugins/Extensions/UnifiProtectDisplay/2

That index is not ours to choose: ``Screens/ButtonSetup.py`` builds it by
walking the registered plugins in name order and counting how many it has
already seen per directory. :func:`plugin_indices` reproduces that counting
exactly, so the binding keeps working when descriptors are added or renamed
instead of silently pointing at the wrong action.

The counting rule and the ``_long`` suffix are the only two things this
module knows about enigma2, and both are pure string work -- which is why it
is all testable off the box.
"""

# The action openATV assigns to whichever key the remote calls "PiP".
PIP_ACTION = "Infobar/showPiP"

# How ButtonSetup names a plugin: the tail of its path from "Plugins".
PLUGIN_PATH = "Plugins/Extensions/UnifiProtectDisplay"

LONG_SUFFIX = "_long"

# Fallbacks, in preference order, for a box where no key is bound to PiP at
# all. f6 is openATV's dedicated PIP key; the others are common stand-ins.
FALLBACK_KEYS = ("f6", "f2", "f1", "f3", "f4")


def long_key(key):
	"""The long-press counterpart of a key name."""
	if not key:
		return None
	return key if key.endswith(LONG_SUFFIX) else key + LONG_SUFFIX


def short_key(key):
	"""The short-press counterpart of a key name."""
	if not key:
		return None
	return key[:-len(LONG_SUFFIX)] if key.endswith(LONG_SUFFIX) else key


def find_pip_key(assignments, fallbacks=FALLBACK_KEYS):
	"""Which key the remote uses for Picture-in-Picture.

	``assignments`` maps key name to assigned action, as
	``config.misc.ButtonSetup`` holds it. Long-press entries are skipped: we
	want the key itself, not one of its presses.

	Falls back to the first known PiP-ish key that exists in
	``assignments``, so a box where nothing is bound yet still gets a
	sensible one rather than nothing at all.
	"""
	for key, action in sorted(assignments.items()):
		if key.endswith(LONG_SUFFIX):
			continue
		if action == PIP_ACTION:
			return key
	for candidate in fallbacks:
		if candidate in assignments:
			return candidate
	return None


def plugin_indices(entries):
	"""Reproduce ButtonSetup's per-directory numbering.

	``entries`` is an ordered sequence of ``(name, path)``, already in the
	order ButtonSetup sees them -- that is, sorted by name, with earlier
	duplicates of a name dropped. Returns ``{name: index}``.

	Mirrors ``Screens/ButtonSetup.py``: a counter per path, incremented for
	each distinct plugin name found at that path, and a name already seen is
	skipped entirely.
	"""
	counters = {}
	seen = set()
	result = {}
	for name, path in entries:
		if name in seen or not path:
			continue
		counters[path] = counters.get(path, 0) + 1
		result[name] = counters[path]
		seen.add(name)
	return result


def action_for(index, path=PLUGIN_PATH):
	"""The ButtonSetup action string for one of our descriptors."""
	return "%s/%d" % (path, int(index))


def desired_assignments(entries, pip_key, short_name, long_name):
	"""Work out what the two key entries should be set to.

	Returns ``{key_name: action}`` for the short and long press, or an empty
	mapping when either descriptor is missing -- better to leave the user's
	keys alone than to bind one half of a pair.
	"""
	if not pip_key:
		return {}
	indices = plugin_indices(entries)
	if short_name not in indices or long_name not in indices:
		return {}
	return {
		short_key(pip_key): action_for(indices[short_name]),
		long_key(pip_key): action_for(indices[long_name]),
	}
