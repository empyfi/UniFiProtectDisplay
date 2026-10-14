# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Data the plugin keeps about cameras and their streams.

Parsing is deliberately lenient. The Protect Integration API adds fields
between releases, and a console a few versions ahead of what we tested against
must not break the camera list. Anything we do not understand is ignored; only
``id`` is strictly required, because without it no other call can be made.
"""

QUALITY_HIGH = "high"
QUALITY_MEDIUM = "medium"
QUALITY_LOW = "low"
QUALITY_PACKAGE = "package"

# Order matters: used as the fallback search order when the configured quality
# is not available on a given camera. Highest fidelity first, with the doorbell
# package camera last since it is a different lens, not a quality tier.
QUALITY_ORDER = (QUALITY_HIGH, QUALITY_MEDIUM, QUALITY_LOW, QUALITY_PACKAGE)

# Protect serves RTSPS on 7441. The plain-RTSP variant some firmware also
# exposes lives on 7447; see to_plain_rtsp().
RTSPS_PORT = 7441
RTSP_PORT = 7447


class Camera(object):
	"""One camera as the Integration API reports it."""

	__slots__ = ("id", "name", "connected", "raw")

	def __init__(self, id, name=None, connected=True, raw=None):
		self.id = id
		self.name = name or id
		self.connected = connected
		self.raw = raw or {}

	@classmethod
	def from_json(cls, data):
		"""Build from one entry of ``GET /v1/cameras``.

		Returns ``None`` for an entry without an id rather than raising: one
		malformed camera should not cost the user the whole list.
		"""
		if not isinstance(data, dict):
			return None
		camera_id = data.get("id")
		if not camera_id:
			return None
		# Protect has used both a boolean and a state string over time, and
		# omits the field entirely on some versions. Absent means "assume
		# usable" so a camera is never hidden because of a field we misread.
		connected = True
		if "isConnected" in data:
			connected = bool(data.get("isConnected"))
		elif "state" in data:
			connected = str(data.get("state")).upper() == "CONNECTED"
		return cls(
			id=str(camera_id),
			name=data.get("name") or str(camera_id),
			connected=connected,
			raw=data,
		)

	def __repr__(self):
		return "<Camera %s %r connected=%s>" % (self.id, self.name, self.connected)

	def __eq__(self, other):
		return isinstance(other, Camera) and other.id == self.id

	def __hash__(self):
		return hash(self.id)


class StreamSet(object):
	"""The quality -> URL map returned by ``/v1/cameras/{id}/rtsps-stream``.

	The endpoint returns a flat object keyed by quality whose values are URLs
	or null. Qualities are not a fixed set (dual-sensor and doorbell cameras
	add their own), so the mapping is kept dynamic rather than modelled as
	fixed attributes.
	"""

	__slots__ = ("streams",)

	def __init__(self, streams=None):
		self.streams = {}
		for quality, url in (streams or {}).items():
			if isinstance(url, str) and url:
				self.streams[str(quality)] = url

	@classmethod
	def from_json(cls, data):
		return cls(data if isinstance(data, dict) else {})

	def available(self):
		"""Qualities that actually have a URL, in QUALITY_ORDER then alphabetical."""
		known = [q for q in QUALITY_ORDER if q in self.streams]
		extra = sorted(q for q in self.streams if q not in QUALITY_ORDER)
		return known + extra

	def url_for(self, quality, fallback=True):
		"""URL for ``quality``, or the next best one when ``fallback``.

		Falling back matters because the user picks one quality globally in
		setup, but a given camera may not have that tier enabled. Without a
		fallback such a camera would silently show nothing.
		"""
		url = self.streams.get(quality)
		if url or not fallback:
			return url
		for candidate in self.available():
			return self.streams[candidate]
		return None

	def __len__(self):
		return len(self.streams)

	def __repr__(self):
		return "<StreamSet %s>" % (",".join(self.available()) or "empty",)


def to_plain_rtsp(url):
	"""Convert Protect's ``rtsps://host:7441/token?enableSrtp`` to plain RTSP.

	Some ffmpeg and GStreamer builds on set-top boxes cannot complete the TLS
	handshake against the console's self-signed certificate. Protect firmware
	that still serves unencrypted RTSP does so on 7447 with the same token and
	no query string, so this rewrite is the documented escape hatch.

	Returns ``None`` when ``url`` is not an rtsps URL, so a caller can tell
	"nothing to convert" from "converted".
	"""
	if not url or not url.startswith("rtsps://"):
		return None
	rest = url[len("rtsps://"):]
	# Drop the query: ?enableSrtp is meaningless without SRTP, and Protect
	# rejects unknown params on the plain port.
	rest = rest.split("?", 1)[0]
	authority, slash, path = rest.partition("/")
	host = authority.rsplit(":", 1)[0] if ":" in authority else authority
	port = RTSP_PORT
	if ":" in authority:
		try:
			# Only remap the port we know about. A console reached through a
			# forward on some other port keeps it, since we cannot guess the
			# matching plain-RTSP forward.
			given = int(authority.rsplit(":", 1)[1])
			port = RTSP_PORT if given == RTSPS_PORT else given
		except ValueError:
			host = authority
	return "rtsp://%s:%d%s%s" % (host, port, slash, path)
