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


LOG_PATH = "/tmp/unifiprotectdisplay.log"

# Enough to cover a session's worth of diagnosis, small enough that tmpfs on
# a receiver with little RAM is never a concern. Past this the file starts
# again rather than growing without bound.
LOG_MAX_BYTES = 256 * 1024


class Logger(object):
	"""Prefixed writer to the enigma2 log and to a file of our own.

	The file is not redundant. On many images enigma2's stdout goes to
	``/dev/console``, where ``print`` output is shown on a screen nobody is
	watching and is never written anywhere -- so the usual advice of "look
	in the enigma2 log" yields nothing at all. Keeping a small file in tmpfs
	means a problem on the box can actually be diagnosed afterwards.

	Inject ``writer`` in tests to assert on output, and ``log_path=None`` to
	switch the file off.
	"""

	def __init__(self, writer=None, debug=False, log_path=LOG_PATH,
			max_bytes=LOG_MAX_BYTES, opener=None, size_of=None):
		self._writer = writer if writer is not None else _print_writer
		self.debug_enabled = debug
		self._log_path = log_path
		self._max_bytes = max_bytes
		self._opener = opener or _open_append
		self._size_of = size_of or _size_of

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
		line = "%s %s" % (PREFIX, message)
		self._writer(line)
		self._to_file(line)

	def _to_file(self, line):
		"""Append one line to our own log. Never raises.

		Logging that can fail a caller is worse than no logging: a read-only
		or full /tmp must not take down the camera display.
		"""
		if not self._log_path:
			return
		try:
			if self._size_of(self._log_path) > self._max_bytes:
				mode = "w"  # start again rather than grow without bound
			else:
				mode = "a"
			handle = self._opener(self._log_path, mode)
			try:
				handle.write("%s %s\n" % (_timestamp(), line))
			finally:
				handle.close()
		except (OSError, IOError, ValueError):
			pass


def _timestamp():
	import time
	return time.strftime("%Y-%m-%d %H:%M:%S")


def _size_of(path):
	import os
	try:
		return os.path.getsize(path)
	except (OSError, IOError):
		return 0


def _open_append(path, mode):
	return open(path, mode)


def _print_writer(line):
	print(line)
