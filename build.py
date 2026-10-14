#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the installable .ipk.

Pure Python on purpose. An ipk is an ar archive holding three members, and
reproducing that here means the package can be built on any machine with a
Python interpreter -- no opkg-utils, no dpkg, no OpenEmbedded checkout, and
the same result on Windows as on the receiver.

	python build.py [--output DIR]

The version is read from the plugin's own ``version.py`` so the package and
the running code can never disagree.
"""

import argparse
import gzip
import io
import os
import re
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_PATH = os.path.join(
	"usr", "lib", "enigma2", "python", "Plugins", "Extensions",
	"UnifiProtectDisplay")
SRC_ROOT = os.path.join(HERE, "src")
CONTROL_DIR = os.path.join(HERE, "CONTROL")
PO_DIR = os.path.join(HERE, "po")

# Never ship build leftovers: a stale .pyc next to a changed .py is a real
# source of "my fix did not take effect" on a receiver.
EXCLUDE_DIRS = {"__pycache__", ".git", ".pytest_cache"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".swp")


def read_version():
	path = os.path.join(SRC_ROOT, PLUGIN_PATH, "version.py")
	with open(path, "r", encoding="utf-8") as handle:
		match = re.search(r'PLUGIN_VERSION\s*=\s*"([^"]+)"', handle.read())
	if not match:
		raise SystemExit("Could not read PLUGIN_VERSION from %s" % path)
	return match.group(1)


def read_package_name():
	with open(os.path.join(CONTROL_DIR, "control"), "r", encoding="utf-8") as h:
		match = re.search(r"^Package:\s*(\S+)", h.read(), re.MULTILINE)
	if not match:
		raise SystemExit("CONTROL/control has no Package: line")
	return match.group(1)


def control_with_version(version):
	"""Return control text with Version: forced to ``version``."""
	with open(os.path.join(CONTROL_DIR, "control"), "r", encoding="utf-8") as h:
		text = h.read()
	return re.sub(
		r"^Version:.*$", "Version: %s" % version, text, count=1,
		flags=re.MULTILINE)


def should_skip(path):
	return any(path.endswith(suffix) for suffix in EXCLUDE_SUFFIXES)


def parse_po(text):
	"""Read a po file into ``{msgid: msgstr}``.

	Enough of the format for this project: single-line and continued
	strings, comments, and the header entry (msgid ""), which is kept
	because gettext expects it in the compiled catalogue.
	"""
	entries = {}
	msgid = None
	msgstr = None
	target = None

	def unquote(fragment):
		fragment = fragment.strip()
		if fragment.startswith('"') and fragment.endswith('"'):
			fragment = fragment[1:-1]
		return (fragment
			.replace("\\n", "\n").replace("\\t", "\t")
			.replace('\\"', '"').replace("\\\\", "\\"))

	for raw in text.split("\n"):
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		if line.startswith("msgid "):
			if msgid is not None:
				entries[msgid] = msgstr or ""
			msgid, msgstr, target = unquote(line[6:]), None, "id"
		elif line.startswith("msgstr "):
			msgstr, target = unquote(line[7:]), "str"
		elif line.startswith('"'):
			if target == "id":
				msgid += unquote(line)
			elif target == "str":
				msgstr += unquote(line)
	if msgid is not None:
		entries[msgid] = msgstr or ""
	return entries


def compile_mo(entries):
	"""Produce a binary .mo catalogue.

	Written out here rather than shelling out to msgfmt so the build needs
	nothing but Python, which is the whole point of this script. The format
	is the GNU one: a magic number, two string tables, and the offsets into
	them; the optional hash table is omitted, which gettext accepts.
	"""
	import struct

	# Untranslated entries are dropped: gettext falls back to the msgid, and
	# an empty translation in the table would render as an empty string.
	usable = sorted(
		(key, value) for key, value in entries.items()
		if value or key == "")
	ids = b"\x00".join(key.encode("utf-8") for key, _ in usable)
	strings = b"\x00".join(value.encode("utf-8") for _, value in usable)

	count = len(usable)
	key_table_offset = 7 * 4
	value_table_offset = key_table_offset + count * 8
	ids_offset = value_table_offset + count * 8
	strings_offset = ids_offset + len(ids) + (1 if count else 0)

	key_table = b""
	value_table = b""
	offset = ids_offset
	for key, _ in usable:
		encoded = key.encode("utf-8")
		key_table += struct.pack("<II", len(encoded), offset)
		offset += len(encoded) + 1
	offset = strings_offset
	for _, value in usable:
		encoded = value.encode("utf-8")
		value_table += struct.pack("<II", len(encoded), offset)
		offset += len(encoded) + 1

	header = struct.pack(
		"<Iiiiiii",
		0x950412DE,  # little-endian magic
		0,  # format revision
		count,
		key_table_offset,
		value_table_offset,
		0,  # hash table size: omitted
		0)  # hash table offset
	return (header + key_table + value_table
		+ ids + (b"\x00" if count else b"")
		+ strings + (b"\x00" if count else b""))


def compile_translations(log=print):
	"""Turn po/*.po into locale/<lang>/LC_MESSAGES/<domain>.mo."""
	if not os.path.isdir(PO_DIR):
		return []
	built = []
	for entry in sorted(os.listdir(PO_DIR)):
		if not entry.endswith(".po"):
			continue
		language = entry[:-3]
		with open(os.path.join(PO_DIR, entry), "r", encoding="utf-8") as handle:
			catalogue = compile_mo(parse_po(handle.read()))
		target_dir = os.path.join(
			SRC_ROOT, PLUGIN_PATH, "locale", language, "LC_MESSAGES")
		os.makedirs(target_dir, exist_ok=True)
		with open(os.path.join(target_dir, "UnifiProtectDisplay.mo"), "wb") as out:
			out.write(catalogue)
		built.append(language)
	if built:
		log("compiled translations: %s" % ", ".join(built))
	return built


def make_tar(add_entries):
	"""Build a gzipped tar in memory.

	``add_entries(tar)`` does the adding. Timestamps are pinned and owners
	normalised so two builds of the same tree produce identical bytes.
	"""
	raw = io.BytesIO()
	with tarfile.open(fileobj=raw, mode="w") as tar:
		add_entries(tar)
	compressed = io.BytesIO()
	# mtime=0 keeps the gzip header stable across builds.
	with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as gz:
		gz.write(raw.getvalue())
	return compressed.getvalue()


def normalise(info):
	"""Pin ownership and time so builds are reproducible."""
	info.uid = 0
	info.gid = 0
	info.uname = "root"
	info.gname = "root"
	info.mtime = 0
	return info


def build_data_tar():
	def add(tar):
		base = os.path.join(SRC_ROOT, PLUGIN_PATH)
		if not os.path.isdir(base):
			raise SystemExit("Plugin sources not found at %s" % base)
		for root, dirs, files in os.walk(base):
			dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
			for name in sorted(files):
				full = os.path.join(root, name)
				if should_skip(full):
					continue
				relative = os.path.relpath(full, SRC_ROOT).replace(os.sep, "/")
				info = tar.gettarinfo(full, arcname="./" + relative)
				normalise(info)
				info.mode = 0o644
				with open(full, "rb") as handle:
					tar.addfile(info, handle)
	return make_tar(add)


def build_control_tar(version):
	def add(tar):
		text = control_with_version(version).encode("utf-8")
		info = tarfile.TarInfo("./control")
		info.size = len(text)
		info.mode = 0o644
		normalise(info)
		tar.addfile(info, io.BytesIO(text))
		for script in ("preinst", "postinst", "prerm", "postrm"):
			path = os.path.join(CONTROL_DIR, script)
			if not os.path.isfile(path):
				continue
			with open(path, "rb") as handle:
				payload = handle.read()
			info = tarfile.TarInfo("./" + script)
			info.size = len(payload)
			# Maintainer scripts must be executable or opkg skips them.
			info.mode = 0o755
			normalise(info)
			tar.addfile(info, io.BytesIO(payload))
	return make_tar(add)


def write_ipk(path, control_tar, data_tar):
	"""Write the ar archive that an ipk actually is."""
	def header(name, size):
		# ar: name(16) mtime(12) uid(6) gid(6) mode(8) size(10) magic(2)
		return (
			"%-16s%-12d%-6d%-6d%-8s%-10d`\n"
			% (name, 0, 0, 0, "100644", size)).encode("ascii")

	members = [
		("debian-binary", b"2.0\n"),
		("control.tar.gz", control_tar),
		("data.tar.gz", data_tar),
	]
	with open(path, "wb") as out:
		out.write(b"!<arch>\n")
		for name, payload in members:
			out.write(header(name, len(payload)))
			out.write(payload)
			# ar members are padded to an even length.
			if len(payload) % 2:
				out.write(b"\n")


def main(argv=None):
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--output", default=os.path.join(HERE, "dist"),
		help="directory for the .ipk (default: ./dist)")
	parser.add_argument(
		"--no-translations", action="store_true",
		help="skip compiling po files even if msgfmt is available")
	args = parser.parse_args(argv)

	version = read_version()
	package = read_package_name()
	if not args.no_translations:
		compile_translations()

	os.makedirs(args.output, exist_ok=True)
	target = os.path.join(args.output, "%s_%s_all.ipk" % (package, version))
	write_ipk(target, build_control_tar(version), build_data_tar())

	size = os.path.getsize(target)
	print("built %s (%.1f KiB)" % (target, size / 1024.0))
	print("install with: opkg install --force-reinstall %s"
		% os.path.basename(target))
	return 0


if __name__ == "__main__":
	sys.exit(main())
