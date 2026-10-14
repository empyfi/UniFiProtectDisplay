# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Logging that is safe to leave switched on in a release.

Enigma2 has no logging framework of its own; plugins print to stdout and the
lines end up in the enigma2 log. Everything here therefore goes through one
prefixed writer, which keeps our lines greppable among enigma2's own output.

The redaction helpers exist because this plugin handles a UniFi Protect API key
and camera stream tokens. Both are bearer credentials: anyone holding one can
watch the cameras. Enigma2 logs are routinely posted in support forums, so a
credential that reaches the log has to be treated as disclosed. Call
:func:`redact` on anything derived from user input before logging it.
"""

PREFIX = "[UnifiProtectDisplay]"

# A Protect stream token is the path segment of an rtsps:// URL and is itself a
# credential, so URLs are logged host-only.
_SCHEME_SEP = "://"


def redact(value, keep=0):
	"""Return a log-safe stand-in for a secret.

	``keep`` trailing characters survive so two different keys can be told
	apart in a log without either being usable. The length is deliberately not
	revealed: with ``keep=0`` every secret looks the same.
	"""
	if not value:
		return "(unset)"
	text = str(value)
	if keep > 0 and len(text) > keep:
		return "***" + text[-keep:]
	return "***"


def redact_url(url):
	"""Return ``scheme://host:port`` and drop path, query and userinfo.

	For Protect the path *is* the stream token, so this is the only form of a
	camera URL that may be logged.
	"""
	if not url:
		return "(unset)"
	text = str(url)
	head, sep, rest = text.partition(_SCHEME_SEP)
	if not sep:
		return "***"
	authority = rest.split("/", 1)[0]
	# Drop any user:password@ prefix.
	authority = authority.rsplit("@", 1)[-1]
	return "%s%s%s/***" % (head, _SCHEME_SEP, authority)


class Logger(object):
	"""Prefixed writer. Inject a fake ``writer`` in tests to assert on output."""

	def __init__(self, writer=None, debug=False):
		self._writer = writer if writer is not None else _print_writer
		self.debug_enabled = debug

	def info(self, message, *args):
		self._emit(message, args)

	def error(self, message, *args):
		self._emit("ERROR: " + message, args)

	def debug(self, message, *args):
		if self.debug_enabled:
			self._emit("debug: " + message, args)

	def _emit(self, message, args):
		if args:
			try:
				message = message % args
			except (TypeError, ValueError):
				# A broken format string must never take down a caller that was
				# only trying to log something.
				message = "%s %r" % (message, (args,))
		self._writer("%s %s" % (PREFIX, message))


def _print_writer(line):
	print(line)
