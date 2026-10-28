# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Putting the cameras in the channel list.

``bouquets.tv`` lists every bouquet the user has, so the editing rules here
matter more than the file this plugin writes: losing one of their own
bouquets would be a worse bug than anything else in the project.
"""

import pytest

from UnifiProtectDisplay import bouquet
from UnifiProtectDisplay import servicerefs


class TestLabels:
	def test_cameras_are_numbered(self):
		# A channel list is read by position, and Protect happily lets two
		# cameras share a name.
		assert bouquet.camera_label(1, "Hof") == "Cam 1 - Hof"
		assert bouquet.camera_label(2, "Tor") == "Cam 2 - Tor"

	def test_a_nameless_camera_is_just_the_number(self):
		assert bouquet.camera_label(3, "") == "Cam 3"
		assert bouquet.camera_label(3, None) == "Cam 3"

	def test_duplicate_names_stay_distinguishable(self):
		# The real case: two cameras both called "G3 Flex".
		labels = bouquet.labels_for([("a", "G3 Flex"), ("b", "G3 Flex")])
		assert labels == ["Cam 1 - G3 Flex", "Cam 2 - G3 Flex"]
		assert len(set(labels)) == 2

	def test_numbering_starts_at_one(self):
		labels = bouquet.labels_for([("a", "Hof"), ("b", "Tor"), ("c", "Weg")])
		assert labels[0].startswith("Cam 1")
		assert labels[2].startswith("Cam 3")

	def test_empty_list(self):
		assert bouquet.labels_for([]) == []


class TestBuildBouquet:
	ENTRIES = [
		("Cam 1 - Hof", "1:0:1:0:0:0:0:0:0:0:http%3a//127.0.0.1%3a8098/cam/a:Cam 1"),
		("Cam 2 - Tor", "1:0:1:0:0:0:0:0:0:0:http%3a//127.0.0.1%3a8098/cam/b:Cam 2"),
	]

	def test_starts_with_the_bouquet_name(self):
		assert bouquet.build_bouquet(self.ENTRIES).startswith("#NAME ")

	def test_each_camera_gets_a_service_and_a_description(self):
		text = bouquet.build_bouquet(self.ENTRIES)
		assert text.count("#SERVICE ") == 2
		assert text.count("#DESCRIPTION ") == 2

	def test_the_reference_is_written_verbatim(self):
		# Escaping belongs to servicerefs; this must not touch it.
		text = bouquet.build_bouquet(self.ENTRIES)
		assert self.ENTRIES[0][1] in text

	def test_ends_with_a_newline(self):
		assert bouquet.build_bouquet(self.ENTRIES).endswith("\n")

	def test_custom_name(self):
		assert "#NAME Kameras" in bouquet.build_bouquet(self.ENTRIES, "Kameras")

	def test_empty_bouquet_is_still_valid(self):
		assert bouquet.build_bouquet([]).startswith("#NAME ")


class TestIndexEditing:
	EXISTING = (
		'#NAME User - bouquets (TV)\n'
		'#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.fav.tv" '
		'ORDER BY bouquet\n'
		'#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.iptv.tv" '
		'ORDER BY bouquet\n'
	)

	def test_other_bouquets_are_preserved_in_order(self):
		result = bouquet.add_to_index(self.EXISTING)
		lines = [l for l in result.split("\n") if l.strip()]
		assert lines[0] == "#NAME User - bouquets (TV)"
		assert "userbouquet.fav.tv" in lines[1]
		assert "userbouquet.iptv.tv" in lines[2]

	def test_ours_is_appended_last(self):
		# Appending keeps the cameras out of the way of existing channels.
		result = bouquet.add_to_index(self.EXISTING)
		assert result.strip().split("\n")[-1] == bouquet.INDEX_LINE

	def test_adding_twice_does_not_duplicate(self):
		once = bouquet.add_to_index(self.EXISTING)
		twice = bouquet.add_to_index(once)
		assert twice.count(bouquet.BOUQUET_FILE) == 1

	def test_the_file_does_not_grow_blank_lines(self):
		result = self.EXISTING
		for _ in range(3):
			result = bouquet.add_to_index(result)
		assert "\n\n" not in result

	def test_adding_to_an_empty_index(self):
		assert bouquet.BOUQUET_FILE in bouquet.add_to_index("")

	def test_removal_keeps_everything_else(self):
		result = bouquet.remove_from_index(bouquet.add_to_index(self.EXISTING))
		assert bouquet.BOUQUET_FILE not in result
		assert "userbouquet.fav.tv" in result
		assert "userbouquet.iptv.tv" in result
		assert "#NAME User - bouquets (TV)" in result

	def test_removal_when_not_present_changes_nothing_meaningful(self):
		result = bouquet.remove_from_index(self.EXISTING)
		for line in self.EXISTING.strip().split("\n"):
			assert line in result

	def test_removal_from_an_empty_index(self):
		assert bouquet.remove_from_index("") == ""


class TestReferencesAreTypeOne:
	def test_entries_use_the_dvb_service_type(self):
		# Type 1 reads HTTP MPEG-TS natively, is the only type PiP accepts,
		# and the channel list treats it as an ordinary service. 4097 would
		# give up two of those three.
		reference = servicerefs.build_pip_reference(
			"http://127.0.0.1:8098/cam/tok", "Cam 1")
		assert reference.split(":")[0] == "1"

	def test_the_url_survives_into_the_bouquet(self):
		url = "http://127.0.0.1:8098/cam/tok"
		reference = servicerefs.build_pip_reference(url, "Cam 1")
		text = bouquet.build_bouquet([("Cam 1", reference)])
		line = [l for l in text.split("\n") if l.startswith("#SERVICE")][0]
		rest = line[len("#SERVICE "):].split(":", 10)[10]
		assert servicerefs.decode_component(rest.split(":", 1)[0]) == url


class TestPaths:
	def test_bouquet_path_is_under_the_configured_directory(self):
		assert bouquet.bouquet_path("/tmp").startswith("/tmp/")

	def test_index_path_names_bouquets_tv(self):
		assert bouquet.index_path("/tmp").endswith("bouquets.tv")

	@pytest.mark.parametrize("directory", ["/etc/enigma2", "/etc/enigma2/"])
	def test_a_trailing_slash_is_tolerated(self, directory):
		assert "//" not in bouquet.bouquet_path(directory)
