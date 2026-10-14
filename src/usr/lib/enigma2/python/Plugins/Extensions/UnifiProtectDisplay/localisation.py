# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Translations for this plugin's own strings.

Enigma2 installs a global ``_`` for its own catalogue, which does not contain
anything from this plugin. Binding a private domain here means the plugin's
German strings are actually found, and that every module says where its
``_`` comes from instead of relying on a builtin appearing from elsewhere.

The lookup falls back to enigma2's catalogue, so common words the image
already translates ("Cancel", "Save") need no entry of their own.
"""

import gettext

DOMAIN = "UnifiProtectDisplay"
LOCALE_PATH = "Extensions/UnifiProtectDisplay/locale"


def _bind():
	try:
		from Tools.Directories import resolveFilename, SCOPE_PLUGINS
		gettext.bindtextdomain(DOMAIN, resolveFilename(SCOPE_PLUGINS, LOCALE_PATH))
	except Exception:  # noqa: BLE001
		# Off the box (tests, tooling) there is no plugin path and no
		# enigma2 to ask. Untranslated strings are the correct outcome.
		pass


def _(text):
	"""Translate ``text``, preferring this plugin's catalogue.

	``dgettext`` returns the key unchanged when there is no translation, so
	a miss falls through to enigma2's own catalogue before giving up.
	"""
	if not text:
		return text
	try:
		translated = gettext.dgettext(DOMAIN, text)
		if translated != text:
			return translated
		return gettext.gettext(text)
	except Exception:  # noqa: BLE001
		return text


def init():
	"""Bind the domain and re-bind whenever the user changes language."""
	_bind()
	try:
		from Components.Language import language
		language.addCallback(_bind)
	except Exception:  # noqa: BLE001
		pass


def blocker_text(codes):
	"""Render capability blocker codes as one translated sentence.

	The codes come from :mod:`capabilities`, which stays free of gettext so
	it can be tested off the box. Joining happens here so the separator is
	translatable too.
	"""
	from .capabilities import BLOCKER_NO_FFMPEG, BLOCKER_NO_PIP_DECODER
	texts = {
		BLOCKER_NO_PIP_DECODER: _(
			"this box has no second video decoder (no PiP)"),
		BLOCKER_NO_FFMPEG: _("ffmpeg is not installed (opkg install ffmpeg)"),
	}
	if not codes:
		return None
	# An unknown code should still say something useful rather than vanish.
	return "; ".join(texts.get(code, code) for code in codes)


# Bind at import: every module that imports `_` expects it ready to use.
init()
