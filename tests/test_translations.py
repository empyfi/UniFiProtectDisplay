# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""The translation catalogue stays complete and safe to format.

Two different failures are guarded here. A missing translation is cosmetic:
one English line in a German menu. A mismatched format specifier is not --
``"%d von %d" % (a, b)`` against a msgid with one ``%s`` raises at runtime, on
the receiver, in a code path that may only run when something has already
gone wrong. Checking it here costs nothing.
"""

import ast
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, os.pardir)
SOURCE_DIR = os.path.join(
	ROOT, "src", "usr", "lib", "enigma2", "python", "Plugins", "Extensions",
	"UnifiProtectDisplay")
PO_DIR = os.path.join(ROOT, "po")

# %s, %d, %(name)s and so on. Enough to compare two strings for compatibility.
FORMAT_SPEC = re.compile(r"%(?:\([^)]*\))?[-+ #0]*[\d*]*(?:\.[\d*]+)?[a-zA-Z%]")


def source_strings():
	"""Every literal passed to _() in the plugin, as a set."""
	found = set()

	class Collector(ast.NodeVisitor):
		def visit_Call(self, node):
			func = node.func
			if isinstance(func, ast.Name) and func.id == "_" and node.args:
				first = node.args[0]
				if isinstance(first, ast.Constant) and isinstance(first.value, str):
					found.add(first.value)
			self.generic_visit(node)

	for root, dirs, files in os.walk(SOURCE_DIR):
		dirs[:] = [d for d in dirs if d != "__pycache__"]
		for name in files:
			if name.endswith(".py"):
				with open(os.path.join(root, name), "r", encoding="utf-8") as h:
					Collector().visit(ast.parse(h.read()))
	return found


def parse_po(path):
	"""Minimal po reader: returns ``{msgid: msgstr}``, header excluded."""
	entries = {}
	msgid = None
	msgstr = None
	target = None
	with open(path, "r", encoding="utf-8") as handle:
		for raw in handle:
			line = raw.rstrip("\n")
			if line.startswith("#") or not line.strip():
				continue
			if line.startswith("msgid "):
				if msgid is not None:
					entries[msgid] = msgstr or ""
				msgid = _unquote(line[6:])
				msgstr = None
				target = "id"
			elif line.startswith("msgstr "):
				msgstr = _unquote(line[7:])
				target = "str"
			elif line.startswith('"'):
				# Continuation line.
				if target == "id":
					msgid += _unquote(line)
				elif target == "str":
					msgstr += _unquote(line)
	if msgid is not None:
		entries[msgid] = msgstr or ""
	entries.pop("", None)  # the header
	return entries


def _unquote(fragment):
	fragment = fragment.strip()
	if fragment.startswith('"') and fragment.endswith('"'):
		fragment = fragment[1:-1]
	return (fragment
		.replace("\\n", "\n")
		.replace("\\t", "\t")
		.replace('\\"', '"')
		.replace("\\\\", "\\"))


def po_files():
	if not os.path.isdir(PO_DIR):
		return []
	return [
		os.path.join(PO_DIR, name)
		for name in sorted(os.listdir(PO_DIR))
		if name.endswith(".po")
	]


PO_FILES = po_files()


class TestTemplate:
	def test_template_exists(self):
		# Regenerated with tools/extract_strings.py.
		assert os.path.isfile(os.path.join(PO_DIR, "UnifiProtectDisplay.pot"))

	def test_template_covers_every_source_string(self):
		template = parse_po(os.path.join(PO_DIR, "UnifiProtectDisplay.pot"))
		missing = source_strings() - set(template)
		assert not missing, (
			"run tools/extract_strings.py; missing: %s" % sorted(missing)[:5])


@pytest.mark.skipif(not PO_FILES, reason="no translations yet")
class TestTranslations:
	@pytest.mark.parametrize("path", PO_FILES, ids=lambda p: os.path.basename(p))
	def test_every_source_string_is_translated(self, path):
		entries = parse_po(path)
		untranslated = sorted(
			text for text in source_strings()
			if not entries.get(text))
		assert not untranslated, (
			"%s is missing %d translation(s), e.g. %s"
			% (os.path.basename(path), len(untranslated), untranslated[:3]))

	@pytest.mark.parametrize("path", PO_FILES, ids=lambda p: os.path.basename(p))
	def test_format_specifiers_match(self, path):
		# This is the one that would otherwise crash on the box.
		problems = []
		for msgid, msgstr in parse_po(path).items():
			if not msgstr:
				continue
			if sorted(FORMAT_SPEC.findall(msgid)) != sorted(
					FORMAT_SPEC.findall(msgstr)):
				problems.append(msgid)
		assert not problems, (
			"%s has format mismatches: %s"
			% (os.path.basename(path), problems))

	@pytest.mark.parametrize("path", PO_FILES, ids=lambda p: os.path.basename(p))
	def test_no_stale_entries(self, path):
		# A translation for a string no longer in the source is dead weight
		# and usually a sign the template was not regenerated.
		stale = sorted(set(parse_po(path)) - source_strings())
		assert not stale, (
			"%s translates strings that no longer exist: %s"
			% (os.path.basename(path), stale[:5]))
