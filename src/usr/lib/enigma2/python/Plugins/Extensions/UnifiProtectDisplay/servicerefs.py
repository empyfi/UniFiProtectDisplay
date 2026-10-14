# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Building enigma2 service reference strings for camera URLs.

Why this needs its own module
-----------------------------
A service reference is a colon-separated string, and every camera URL contains
colons. Getting the escaping wrong does not raise anything: enigma2 parses the
mangled reference happily and then plays nothing, which is close to
undebuggable from the UI. So the rule is pinned down here, in one place, with
tests.

The reference format is::

	<type>:<flags>:<data0>..<data7>:<path>:<name>

and enigma2's own round-trip is (``lib/service/service.cpp``):

* writing -- ``encode()`` escapes exactly ``:``, ``%`` and control characters
  (below 0x20) as lowercase ``%xx``, and leaves everything else alone;
* reading -- the constructor runs ``urlDecode()`` over both path and name,
  which turns any ``%xx`` back into its byte.

:func:`encode_component` mirrors ``encode()`` character for character. Two
consequences worth stating, because both are easy to get wrong by guessing:

* ``?``, ``&``, ``=`` and ``/`` are *not* escaped, so Protect's
  ``?enableSrtp`` query survives the round trip untouched;
* ``%`` must be escaped to ``%25``, otherwise a URL that already contains a
  percent escape is silently corrupted on the way back.
"""

# Service type ids, as registered by the respective service factories.
SERVICE_TYPE_DVB = 1  # eServiceFactoryDVB - hardware path, the only one with PiP
SERVICE_TYPE_GSTREAMER = 4097  # eServiceFactoryMP3 - always present
SERVICE_TYPE_GSTPLAYER = 5001  # serviceapp, gstplayer backend
SERVICE_TYPE_EXTEPLAYER3 = 5002  # serviceapp, ffmpeg-based backend

# Player ids as stored in config, mapped to the service type they select.
PLAYER_TYPES = {
	"gstreamer": SERVICE_TYPE_GSTREAMER,
	"exteplayer3": SERVICE_TYPE_EXTEPLAYER3,
	"gstplayer": SERVICE_TYPE_GSTPLAYER,
}

# data[0] carries the DVB service type; 1 is "digital television". Players
# ignore it, but the channel list uses it to pick an icon and to decide a
# service is video rather than radio.
_STYPE_TV = 1


def encode_component(value):
	"""Escape one reference component the way enigma2's ``encode()`` does.

	Mirrors ``lib/service/service.cpp``: only ``:``, ``%`` and control
	characters are escaped, as lowercase ``%xx``.
	"""
	if not value:
		return ""
	out = []
	for char in str(value):
		code = ord(char)
		if char == ":" or char == "%" or code < 32:
			out.append("%%%02x" % code)
		else:
			out.append(char)
	return "".join(out)


def decode_component(value):
	"""Inverse of :func:`encode_component`, matching enigma2's ``urlDecode()``.

	Only needed to read back references the plugin wrote (and in tests, to
	assert the round trip). Malformed trailing escapes are dropped, as
	enigma2 drops them.
	"""
	if not value:
		return ""
	text = str(value)
	out = []
	index = 0
	length = len(text)
	while index < length:
		char = text[index]
		if char != "%":
			out.append(char)
			index += 1
			continue
		if index + 2 >= length:
			# A truncated escape at the very end: enigma2 breaks out of the
			# loop and drops it rather than emitting a stray '%'.
			break
		pair = text[index + 1:index + 3]
		try:
			code = int(pair, 16)
		except ValueError:
			# enigma2 feeds the pair to strtoul, which yields 0 for garbage,
			# and a decoded 0 byte is dropped. Same outcome: skip it.
			index += 3
			continue
		if code:
			out.append(chr(code))
		index += 3
	return "".join(out)


def build_reference(url, name="", service_type=SERVICE_TYPE_GSTREAMER):
	"""Return a full service reference string for a stream ``url``.

	``name`` is what the channel list and the info bar show; it is escaped the
	same way as the path, so a camera called "Hof: Tor" cannot split the
	reference.
	"""
	if not url:
		raise ValueError("A service reference needs a URL")
	fields = [str(int(service_type)), "0"]
	# data[0]..data[7], all hex. Only data[0] is meaningful for a stream.
	fields.append("%x" % _STYPE_TV)
	fields.extend(["0"] * 7)
	reference = ":".join(fields) + ":" + encode_component(url)
	if name:
		reference += ":" + encode_component(name)
	return reference


def build_stream_reference(url, name="", player="gstreamer"):
	"""Reference for playing ``url`` on the main decoder with a software player.

	``player`` is a key of :data:`PLAYER_TYPES`; an unknown value falls back
	to GStreamer, which is the one backend present on every image.
	"""
	service_type = PLAYER_TYPES.get(player, SERVICE_TYPE_GSTREAMER)
	return build_reference(url, name, service_type)


def build_pip_reference(url, name=""):
	"""Reference for the PiP decoder.

	Deliberately type 1. PiP calls ``setTarget(1)`` on the service, and that
	is implemented only by ``eDVBServicePlay``; every software player returns
	-1 and PiP then reports "Incorrect type service for PiP!". So the URL
	handed in here has to be HTTP carrying MPEG-TS -- which is exactly what
	``player.tsproxy`` produces from RTSP. Note that enigma2's own
	``PictureInPicture.playService()`` performs the same 4097 -> 1 rewrite.
	"""
	return build_reference(url, name, SERVICE_TYPE_DVB)


def is_stream_reference(reference):
	"""True when ``reference`` points at a URL rather than a tuner or a file."""
	if not reference:
		return False
	parts = str(reference).split(":", 10)
	if len(parts) < 11:
		return False
	return "//" in decode_component(parts[10].split(":", 1)[0])
