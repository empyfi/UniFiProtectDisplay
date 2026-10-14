#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regenerate po/UnifiProtectDisplay.pot from the plugin sources.

xgettext would do this, but it is not installed everywhere this project is
worked on, and the plugin's usage is simple enough to read off the AST: all
that is wanted is ``_("literal")``. Parsing rather than grepping means a
string spanning several lines, or one built by implicit concatenation, is
picked up exactly as Python sees it.

	python tools/extract_strings.py

Existing po files are left alone; merge with ``msgmerge`` when it is
available.
"""

import ast
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, os.pardir)
SOURCE_DIR = os.path.join(
	ROOT, "src", "usr", "lib", "enigma2", "python", "Plugins", "Extensions",
	"UnifiProtectDisplay")
POT_PATH = os.path.join(ROOT, "po", "UnifiProtectDisplay.pot")

HEADER = '''# Translation template for the UniFi Protect Display plugin.
# Copyright (C) 2026 empyfi
# This file is distributed under the same licence as the plugin.
#
msgid ""
msgstr ""
"Project-Id-Version: UnifiProtectDisplay\\n"
"Report-Msgid-Bugs-To: \\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"
'''


class Collector(ast.NodeVisitor):
	def __init__(self, relative_path, into):
		self.path = relative_path
		self.into = into

	def visit_Call(self, node):
		func = node.func
		if isinstance(func, ast.Name) and func.id == "_" and node.args:
			first = node.args[0]
			if isinstance(first, ast.Constant) and isinstance(first.value, str):
				self.into.setdefault(first.value, []).append(
					"%s:%d" % (self.path, node.lineno))
		self.generic_visit(node)


def escape(text):
	return (text
		.replace("\\", "\\\\")
		.replace('"', '\\"')
		.replace("\n", "\\n")
		.replace("\t", "\\t"))


def collect():
	strings = collections.OrderedDict()
	for root, dirs, files in os.walk(SOURCE_DIR):
		dirs[:] = sorted(d for d in dirs if d != "__pycache__")
		for name in sorted(files):
			if not name.endswith(".py"):
				continue
			full = os.path.join(root, name)
			relative = os.path.relpath(full, ROOT).replace(os.sep, "/")
			with open(full, "r", encoding="utf-8") as handle:
				tree = ast.parse(handle.read())
			Collector(relative, strings).visit(tree)
	return strings


def main():
	strings = collect()
	os.makedirs(os.path.dirname(POT_PATH), exist_ok=True)
	with open(POT_PATH, "w", encoding="utf-8", newline="\n") as out:
		out.write(HEADER)
		for text, locations in strings.items():
			out.write("\n")
			for location in locations:
				out.write("#: %s\n" % location)
			out.write('msgid "%s"\n' % escape(text))
			out.write('msgstr ""\n')
	print("wrote %s (%d strings)" % (os.path.relpath(POT_PATH, ROOT), len(strings)))
	return 0


if __name__ == "__main__":
	sys.exit(main())
