# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""The packaging script.

Two parts of build.py are hand-written binary formats -- the gettext .mo
catalogue and the ar archive an ipk is -- so neither can be taken on trust.
The .mo is checked by loading it back with Python's own gettext, which is a
different implementation from the one that wrote it.
"""

import gettext
import gzip
import importlib.util
import io
import os
import struct
import tarfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_PY = os.path.join(HERE, os.pardir, "build.py")


def load_build():
	spec = importlib.util.spec_from_file_location("build_module", BUILD_PY)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


build = load_build()


SAMPLE_PO = '''# A comment, ignored.
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Cameras"
msgstr "Kameras"

#: some/file.py:12
msgid "%d of %d selected"
msgstr "%d von %d ausgewählt"

msgid "Multi "
"line source"
msgstr "Mehrzeilige "
"Quelle"

msgid "Untranslated"
msgstr ""
'''


class TestPoParsing:
	def test_reads_simple_entries(self):
		entries = build.parse_po(SAMPLE_PO)
		assert entries["Cameras"] == "Kameras"

	def test_joins_continued_strings(self):
		entries = build.parse_po(SAMPLE_PO)
		assert entries["Multi line source"] == "Mehrzeilige Quelle"

	def test_keeps_the_header_entry(self):
		# gettext expects the metadata entry in the catalogue.
		assert "" in build.parse_po(SAMPLE_PO)

	def test_comments_are_ignored(self):
		assert "# A comment, ignored." not in build.parse_po(SAMPLE_PO)

	def test_empty_translation_is_kept_as_empty(self):
		assert build.parse_po(SAMPLE_PO)["Untranslated"] == ""


class TestMoCompilation:
	@pytest.fixture
	def catalogue(self):
		data = build.compile_mo(build.parse_po(SAMPLE_PO))
		return gettext.GNUTranslations(io.BytesIO(data))

	def test_magic_number(self):
		data = build.compile_mo(build.parse_po(SAMPLE_PO))
		assert struct.unpack("<I", data[:4])[0] == 0x950412DE

	def test_translations_round_trip(self, catalogue):
		# Read back by a different gettext implementation than the one that
		# wrote it, which is the point of the exercise.
		assert catalogue.gettext("Cameras") == "Kameras"

	def test_non_ascii_survives(self, catalogue):
		assert catalogue.gettext("%d of %d selected") == "%d von %d ausgewählt"

	def test_continued_strings_round_trip(self, catalogue):
		assert catalogue.gettext("Multi line source") == "Mehrzeilige Quelle"

	def test_untranslated_entries_fall_through(self, catalogue):
		# Dropped from the table on purpose: an empty translation in the
		# catalogue would render as an empty string on screen.
		assert catalogue.gettext("Untranslated") == "Untranslated"

	def test_unknown_string_falls_through(self, catalogue):
		assert catalogue.gettext("never seen") == "never seen"

	def test_empty_catalogue_is_still_valid(self):
		data = build.compile_mo({})
		assert gettext.GNUTranslations(io.BytesIO(data)).gettext("x") == "x"


class TestRealCatalogue:
	def test_shipped_german_catalogue_compiles_and_loads(self):
		po_path = os.path.join(HERE, os.pardir, "po", "de.po")
		if not os.path.isfile(po_path):
			pytest.skip("no German translation in this checkout")
		with open(po_path, "r", encoding="utf-8") as handle:
			entries = build.parse_po(handle.read())
		catalogue = gettext.GNUTranslations(
			io.BytesIO(build.compile_mo(entries)))
		assert catalogue.gettext("Cameras") == "Kameras"


class TestIpk:
	@pytest.fixture
	def archive(self, tmp_path):
		path = str(tmp_path / "test.ipk")
		build.write_ipk(
			path,
			build.build_control_tar(build.read_version()),
			build.build_data_tar())
		return path

	@staticmethod
	def read_ar(path):
		data = open(path, "rb").read()
		assert data.startswith(b"!<arch>\n")
		members = []
		pos = 8
		while pos < len(data):
			header = data[pos:pos + 60]
			pos += 60
			name = header[0:16].decode().strip()
			size = int(header[48:58].decode().strip())
			members.append((name, data[pos:pos + size]))
			pos += size + (size % 2)
		return members

	def test_member_order_matches_the_ipk_format(self, archive):
		# opkg reads these positionally; debian-binary must come first.
		names = [name for name, _ in self.read_ar(archive)]
		assert names == ["debian-binary", "control.tar.gz", "data.tar.gz"]

	def test_debian_binary_version(self, archive):
		members = dict(self.read_ar(archive))
		assert members["debian-binary"] == b"2.0\n"

	def test_control_carries_the_plugin_version(self, archive):
		members = dict(self.read_ar(archive))
		tar = tarfile.open(
			fileobj=io.BytesIO(gzip.decompress(members["control.tar.gz"])))
		control = tar.extractfile("./control").read().decode()
		# The package and the running code must never disagree about which
		# version is installed.
		assert "Version: %s" % build.read_version() in control

	def test_maintainer_scripts_are_executable(self, archive):
		members = dict(self.read_ar(archive))
		tar = tarfile.open(
			fileobj=io.BytesIO(gzip.decompress(members["control.tar.gz"])))
		for name in tar.getnames():
			if name.endswith(("postinst", "prerm")):
				# opkg silently skips a script it cannot execute.
				assert tar.getmember(name).mode == 0o755

	def test_payload_installs_to_the_plugin_path(self, archive):
		members = dict(self.read_ar(archive))
		tar = tarfile.open(
			fileobj=io.BytesIO(gzip.decompress(members["data.tar.gz"])))
		names = tar.getnames()
		assert any(n.endswith(
			"/Plugins/Extensions/UnifiProtectDisplay/plugin.py") for n in names)

	def test_no_build_leftovers_are_shipped(self, archive):
		# A stale .pyc next to a changed .py is a real source of "my fix did
		# not take effect" on a receiver.
		members = dict(self.read_ar(archive))
		tar = tarfile.open(
			fileobj=io.BytesIO(gzip.decompress(members["data.tar.gz"])))
		junk = [
			n for n in tar.getnames()
			if "__pycache__" in n or n.endswith((".pyc", ".pyo"))]
		assert not junk, junk

	def test_build_is_reproducible(self, tmp_path):
		# Pinned mtimes and ownership; two builds of one tree must match.
		first = str(tmp_path / "a.ipk")
		second = str(tmp_path / "b.ipk")
		for path in (first, second):
			build.write_ipk(
				path,
				build.build_control_tar(build.read_version()),
				build.build_data_tar())
		assert open(first, "rb").read() == open(second, "rb").read()
