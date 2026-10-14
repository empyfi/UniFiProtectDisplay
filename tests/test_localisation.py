# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Translation plumbing.

Importable off the box because every enigma2 import in the module is inside a
try/except -- which is itself the behaviour worth pinning down, since the
plugin must not fail to import just because a locale path is missing.
"""

from UnifiProtectDisplay import capabilities
from UnifiProtectDisplay.localisation import _, blocker_text


class TestFallback:
	def test_untranslated_text_passes_through(self):
		# No catalogue off the box; the English string is the right answer.
		assert _("Console address") == "Console address"

	def test_empty_input_is_returned_unchanged(self):
		assert _("") == ""
		assert _(None) is None


class TestBlockerText:
	def test_no_blockers_is_none(self):
		assert blocker_text([]) is None
		assert blocker_text(None) is None

	def test_every_capability_code_has_text(self):
		# A code without a translation would show the user a bare identifier
		# like "no_ffmpeg" instead of something they can act on.
		for code in capabilities.BLOCKER_CODES:
			rendered = blocker_text([code])
			assert rendered
			assert rendered != code

	def test_several_blockers_are_joined(self):
		# The user should fix everything in one pass, not discover the
		# second problem after solving the first.
		rendered = blocker_text(list(capabilities.BLOCKER_CODES))
		assert "; " in rendered

	def test_unknown_code_still_renders(self):
		assert blocker_text(["something_new"]) == "something_new"


class TestCapabilitiesStayTranslatable:
	def test_blockers_returns_codes_not_sentences(self):
		# capabilities must stay free of gettext so it can be tested off the
		# box; it returns codes and the UI renders them.
		caps = capabilities.Capabilities(
			exists=lambda p: False, environ={"PATH": ""})
		codes = caps.remux_pip_blockers()
		assert set(codes) <= set(capabilities.BLOCKER_CODES)
		assert codes

	def test_english_sentence_is_still_available_for_logs(self):
		caps = capabilities.Capabilities(
			exists=lambda p: False, environ={"PATH": ""})
		assert "ffmpeg" in caps.remux_pip_blocker()
