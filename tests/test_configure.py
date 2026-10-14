# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""The configuration pusher.

The important part is :func:`rewrite_settings`. It edits
``/etc/enigma2/settings``, the single file holding every setting on the
receiver -- tuner configuration, bouquets, timers, network. Losing an
unrelated line there is a much worse failure than anything the plugin itself
can do, so what it preserves is spelled out rather than assumed.
"""

import importlib.util
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIGURE_PY = os.path.join(HERE, os.pardir, "tools", "configure.py")


def load_configure():
	spec = importlib.util.spec_from_file_location("configure_module", CONFIGURE_PY)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


configure = load_configure()

# A plausible slice of a real settings file.
EXISTING = """config.misc.firstrun=0
config.av.videomode.HDMI=1080p
config.plugins.unifiprotectdisplay.host=10.0.0.9
config.plugins.unifiprotectdisplay.corner_mode=snapshot
config.timers.first=1
config.usage.setup_level=expert
"""


class TestValueSerialisation:
	@pytest.mark.parametrize("value", [True, "True", "true", "1", "yes", "on"])
	def test_truthy_values_become_True(self, value):
		# ConfigBoolean.toString writes capitalised True/False; anything else
		# would be read back as the default.
		assert configure.to_setting("rotate", value) == "True"

	@pytest.mark.parametrize("value", [False, "False", "false", "0", "", "no"])
	def test_falsy_values_become_False(self, value):
		assert configure.to_setting("rotate", value) == "False"

	def test_plain_values_are_stringified(self):
		assert configure.to_setting("corner_width", 30) == "30"
		assert configure.to_setting("quality", "medium") == "medium"

	def test_every_known_setting_serialises(self):
		for name, (_, default) in configure.SETTINGS.items():
			assert isinstance(configure.to_setting(name, default), str)


class TestRewriteSettings:
	def test_foreign_lines_are_preserved_in_order(self):
		result = configure.rewrite_settings(EXISTING, {"host": "10.0.0.1"})
		lines = result.split("\n")
		foreign = [line for line in lines if line and not line.startswith(
			configure.PREFIX)]
		assert foreign == [
			"config.misc.firstrun=0",
			"config.av.videomode.HDMI=1080p",
			"config.timers.first=1",
			"config.usage.setup_level=expert",
		]

	def test_old_plugin_entries_are_replaced_not_duplicated(self):
		result = configure.rewrite_settings(
			EXISTING, {"host": "10.0.0.1", "corner_mode": "pip"})
		assert result.count("unifiprotectdisplay.host=") == 1
		assert "unifiprotectdisplay.host=10.0.0.1" in result
		assert "unifiprotectdisplay.host=10.0.0.9" not in result
		assert "unifiprotectdisplay.corner_mode=pip" in result
		assert "unifiprotectdisplay.corner_mode=snapshot" not in result

	def test_settings_removed_from_the_map_disappear(self):
		# Writing an empty map must clear our entries, not leave stale ones.
		result = configure.rewrite_settings(EXISTING, {})
		assert configure.PREFIX not in result

	def test_file_ends_with_a_newline(self):
		# Enigma2 reads the file line by line; a missing final newline would
		# make the last entry easy to lose on the next append.
		assert configure.rewrite_settings(EXISTING, {"host": "x"}).endswith("\n")

	def test_no_blank_lines_are_introduced(self):
		result = configure.rewrite_settings(EXISTING, {"host": "x"})
		assert "" not in result.split("\n")[:-1]

	# A file without a trailing newline must not glue a key onto a foreign line.
	def test_handles_a_file_without_trailing_newline(self):
		result = configure.rewrite_settings(
			"config.misc.firstrun=0", {"host": "x"})
		assert "config.misc.firstrun=0" in result.split("\n")

	def test_values_containing_equals_survive(self):
		# An API key is base64-ish and can contain '='.
		result = configure.rewrite_settings(EXISTING, {"apikey": "ab=cd=="})
		assert "unifiprotectdisplay.apikey=ab=cd==" in result

	def test_entries_are_sorted_for_a_stable_diff(self):
		result = configure.rewrite_settings(
			EXISTING, {"quality": "low", "corner": "topleft", "host": "h"})
		ours = [line for line in result.split("\n")
			if line.startswith(configure.PREFIX)]
		assert ours == sorted(ours)


class TestOverrideCollection:
	class Args:
		def __init__(self, **kwargs):
			defaults = dict(
				console=None, verify_ssl=False, mode=None, corner=None,
				width=None, margin=None, quality=None, player=None,
				interval=None, rotate=None, rotate_interval=None,
				plain_rtsp=None, autostart=None, debug=None)
			defaults.update(kwargs)
			for key, value in defaults.items():
				setattr(self, key, value)

	def test_unset_options_are_not_overridden(self):
		# Merge semantics: adjusting one option must not reset the rest.
		assert configure.collect_overrides(self.Args(), None) == {}

	def test_given_options_are_collected(self):
		overrides = configure.collect_overrides(
			self.Args(mode="pip", width=30), None)
		assert overrides == {"corner_mode": "pip", "corner_width": 30}

	def test_switching_something_off_is_an_override(self):
		# False is a real choice, distinct from "not mentioned".
		overrides = configure.collect_overrides(self.Args(rotate=False), None)
		assert overrides == {"rotate": False}

	def test_api_key_is_included_when_present(self):
		assert configure.collect_overrides(self.Args(), "KEY")["apikey"] == "KEY"

	def test_console_maps_to_host(self):
		overrides = configure.collect_overrides(self.Args(console="10.0.0.1"), None)
		assert overrides["host"] == "10.0.0.1"
