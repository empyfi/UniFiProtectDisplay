# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Binding the camera corner to the remote's PiP key.

The fragile part is the index: openATV names a plugin by its directory plus a
number it derives by walking every registered plugin in name order. Getting
that number wrong does not fail loudly -- the key simply runs a different
plugin. So the counting rule is reproduced here and pinned down.
"""

from UnifiProtectDisplay import buttonbinding as bb

PATH = bb.PLUGIN_PATH
OTHER = "Plugins/Extensions/SomethingElse"


class TestKeyNames:
	def test_long_suffix_is_added(self):
		assert bb.long_key("f2") == "f2_long"

	def test_long_key_is_idempotent(self):
		assert bb.long_key("f2_long") == "f2_long"

	def test_short_strips_the_suffix(self):
		assert bb.short_key("f2_long") == "f2"

	def test_short_is_idempotent(self):
		assert bb.short_key("f2") == "f2"

	def test_empty_input(self):
		assert bb.long_key("") is None
		assert bb.short_key(None) is None


class TestFindPipKey:
	def test_finds_the_key_bound_to_pip(self):
		assert bb.find_pip_key({
			"f1": "Infobar/zapUp", "f2": bb.PIP_ACTION}) == "f2"

	def test_ignores_long_press_entries(self):
		# We want the key itself, not one of its presses.
		assert bb.find_pip_key({
			"f2_long": bb.PIP_ACTION, "f6": bb.PIP_ACTION}) == "f6"

	def test_falls_back_to_the_dedicated_pip_key(self):
		# A box where nothing is bound yet should still get something
		# sensible rather than nothing at all.
		assert bb.find_pip_key({"f6": "", "f2": "", "f1": ""}) == "f6"

	def test_fallback_order_is_respected(self):
		assert bb.find_pip_key({"f2": "", "f1": ""}) == "f2"

	def test_an_explicit_binding_beats_the_fallback(self):
		assert bb.find_pip_key({"f6": "", "f3": bb.PIP_ACTION}) == "f3"

	def test_nothing_usable(self):
		assert bb.find_pip_key({"zzz": ""}) is None
		assert bb.find_pip_key({}) is None


class TestPluginIndices:
	def test_counts_per_directory(self):
		# Mirrors ButtonSetup: one counter per path, in name order.
		entries = [("A", PATH), ("B", PATH), ("C", OTHER)]
		assert bb.plugin_indices(entries) == {"A": 1, "B": 2, "C": 1}

	def test_a_repeated_name_is_skipped(self):
		# ButtonSetup keeps a list of names already seen and ignores later
		# duplicates, so they must not consume an index.
		entries = [("A", PATH), ("A", PATH), ("B", PATH)]
		assert bb.plugin_indices(entries) == {"A": 1, "B": 2}

	def test_entries_without_a_path_are_ignored(self):
		entries = [("A", ""), ("B", PATH)]
		assert bb.plugin_indices(entries) == {"B": 1}

	def test_other_plugins_do_not_shift_our_numbering(self):
		# Only plugins in the same directory affect our index.
		entries = [("Aardvark", OTHER), ("Ours", PATH), ("Zebra", OTHER)]
		assert bb.plugin_indices(entries)["Ours"] == 1

	def test_order_decides_the_index(self):
		entries = [("B", PATH), ("A", PATH)]
		assert bb.plugin_indices(entries) == {"B": 1, "A": 2}


class TestActionStrings:
	def test_format_matches_buttonsetup(self):
		assert bb.action_for(2) == PATH + "/2"

	def test_accepts_a_numeric_string(self):
		assert bb.action_for("3") == PATH + "/3"


class TestDesiredAssignments:
	ENTRIES = [
		("UniFi Protect Display", PATH),
		("UniFi Protect: PiP key", PATH),
		("UniFi Protect: PiP key (long)", PATH),
	]

	def test_binds_both_presses_of_one_key(self):
		result = bb.desired_assignments(
			self.ENTRIES, "f2",
			"UniFi Protect: PiP key", "UniFi Protect: PiP key (long)")
		assert result == {"f2": PATH + "/2", "f2_long": PATH + "/3"}

	def test_a_long_key_name_is_accepted_as_the_key(self):
		result = bb.desired_assignments(
			self.ENTRIES, "f2_long",
			"UniFi Protect: PiP key", "UniFi Protect: PiP key (long)")
		assert set(result) == {"f2", "f2_long"}

	def test_nothing_without_a_key(self):
		assert bb.desired_assignments(self.ENTRIES, None, "a", "b") == {}

	def test_nothing_when_a_descriptor_is_missing(self):
		# Binding one half of the pair would leave the key half-broken.
		assert bb.desired_assignments(
			self.ENTRIES, "f2", "UniFi Protect: PiP key", "Not registered") == {}

	def test_indices_shift_with_other_plugins_in_our_directory(self):
		entries = [("AAA first", PATH)] + self.ENTRIES
		result = bb.desired_assignments(
			entries, "f2",
			"UniFi Protect: PiP key", "UniFi Protect: PiP key (long)")
		assert result == {"f2": PATH + "/3", "f2_long": PATH + "/4"}
