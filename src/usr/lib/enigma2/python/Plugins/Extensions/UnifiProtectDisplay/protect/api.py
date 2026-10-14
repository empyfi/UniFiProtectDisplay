# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Client for the UniFi Protect Integration API.

Scope and shape
---------------
Only the five endpoints the plugin needs are implemented, against the public
Integration API (Protect 5.3 and later) rather than the private bootstrap API:
the public one authenticates with a standalone API key instead of the account
password, and its payloads are far more stable across Protect releases.

	GET  /v1/meta/info                        version gate
	GET  /v1/cameras                          camera list
	GET  /v1/cameras/{id}/rtsps-stream        active RTSPS URLs per quality
	POST /v1/cameras/{id}/rtsps-stream        enable a quality, returns URLs
	GET  /v1/cameras/{id}/snapshot            JPEG bytes

Everything is stdlib. openATV images are not guaranteed to carry ``requests``,
and pulling a dependency in for five calls would make the package harder to
install than the plugin is to use.

Blocking
--------
Every method here blocks. Calling one from the enigma2 main loop freezes the
GUI until the console answers, which on an unreachable NVR means until the
timeout expires. Callers must go through ``async_util.run_in_thread``; this
module stays synchronous so it can be tested without a reactor.

TLS
---
A UniFi console presents a self-signed certificate, so verification is off by
default. That is not a free choice: with verification on, every call to a
stock console fails. The exposure is bounded - this is a LAN-local request to
an address the user typed, carrying an API key scoped to Protect.
"""

import json
import socket
import ssl

from . import errors
from .models import Camera, StreamSet

from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener, HTTPSHandler

API_BASE_PATH = "/proxy/protect/integration"
API_KEY_HEADER = "X-API-KEY"
DEFAULT_TIMEOUT = 10.0

# Protect answers some writes with an empty 204 body; that is success, not a
# protocol error.
_NO_BODY_STATUSES = (204, 304)


class Response(object):
	"""What a transport hands back. Small on purpose: easy to fake in tests."""

	__slots__ = ("status", "body", "headers")

	def __init__(self, status, body=b"", headers=None):
		self.status = status
		self.body = body or b""
		self.headers = headers or {}


def urllib_transport(method, url, headers, body, timeout, verify_ssl):
	"""Default transport. Returns a :class:`Response` for any HTTP status.

	HTTP error statuses come back as responses rather than exceptions so that
	all status handling lives in one place (:meth:`ProtectApi._handle`); only
	genuine transport failures raise.
	"""
	context = None
	if url.startswith("https://"):
		context = ssl.create_default_context()
		if not verify_ssl:
			context.check_hostname = False
			context.verify_mode = ssl.CERT_NONE
	opener = build_opener(HTTPSHandler(context=context)) if context else build_opener()
	request = Request(url, data=body)
	for name, value in headers.items():
		request.add_header(name, value)
	# urllib picks the method from whether data is present, so a bodyless POST
	# would silently become a GET. Be explicit.
	request.get_method = lambda: method
	try:
		handle = opener.open(request, timeout=timeout)
	except HTTPError as exc:
		# An HTTPError *is* the response; read it so error bodies stay usable.
		return Response(exc.code, exc.read(), dict(exc.headers or {}))
	except (URLError, socket.timeout, socket.error) as exc:
		raise errors.ProtectUnavailableError(_reason(exc))
	try:
		status = getattr(handle, "status", None) or handle.getcode()
		return Response(status, handle.read(), dict(handle.headers or {}))
	finally:
		handle.close()


def _reason(exc):
	"""Human-readable cause for a transport failure, without a stack trace."""
	reason = getattr(exc, "reason", None) or exc
	return str(reason) or exc.__class__.__name__


class ProtectApi(object):
	"""Talks to one UniFi console.

	``transport`` is injectable so the whole class can be exercised offline;
	the default is :func:`urllib_transport`.
	"""

	def __init__(self, host, api_key, verify_ssl=False, timeout=DEFAULT_TIMEOUT,
			transport=None, log=None):
		self.host = (host or "").strip().rstrip("/")
		self.api_key = (api_key or "").strip()
		self.verify_ssl = verify_ssl
		self.timeout = timeout
		self._transport = transport or urllib_transport
		self._log = log

	# -- configuration ----------------------------------------------------

	def is_configured(self):
		return bool(self.host) and bool(self.api_key)

	def base_url(self):
		"""Console base URL. A bare host gets https://, which is all Protect serves."""
		host = self.host
		if not host:
			return ""
		if not host.startswith("http://") and not host.startswith("https://"):
			host = "https://" + host
		return host.rstrip("/")

	# -- endpoints --------------------------------------------------------

	def meta_info(self):
		"""GET /v1/meta/info - also the cheapest way to validate a key."""
		return self._request("GET", "/v1/meta/info")

	def list_cameras(self):
		"""All cameras the key can see, as :class:`Camera` objects.

		Entries without an id are dropped rather than failing the call, so one
		odd device cannot cost the user the rest of the list.
		"""
		data = self._request("GET", "/v1/cameras")
		if not isinstance(data, list):
			raise errors.ProtectProtocolError(
				"Camera list has unexpected shape (%s)" % type(data).__name__)
		cameras = []
		for entry in data:
			camera = Camera.from_json(entry)
			if camera is not None:
				cameras.append(camera)
		return cameras

	def get_rtsps_streams(self, camera_id):
		"""Currently enabled RTSPS URLs for one camera.

		An empty :class:`StreamSet` means RTSP is off for every quality, which
		is Protect's default; :meth:`create_rtsps_streams` turns one on.
		"""
		data = self._request("GET", "/v1/cameras/%s/rtsps-stream" % _path(camera_id))
		return StreamSet.from_json(data)

	def create_rtsps_streams(self, camera_id, qualities):
		"""Enable RTSPS for ``qualities`` and return the resulting URLs.

		This is what spares the user from enabling RTSP by hand per camera in
		the Protect UI and copying tokens across. It writes to the console, so
		it is only ever issued on an explicit user action.
		"""
		if isinstance(qualities, str):
			qualities = [qualities]
		payload = {"qualities": [str(q) for q in qualities]}
		data = self._request(
			"POST", "/v1/cameras/%s/rtsps-stream" % _path(camera_id), json_body=payload)
		return StreamSet.from_json(data)

	def get_snapshot(self, camera_id, high_quality=False):
		"""Raw JPEG bytes for one camera.

		``high_quality`` asks Protect for 1080p or better. The overlay leaves
		it off: the picture is scaled into a corner anyway, and the larger
		image costs both console time and decode time on the box.
		"""
		params = {"highQuality": "true" if high_quality else "false"}
		return self._request(
			"GET", "/v1/cameras/%s/snapshot" % _path(camera_id),
			params=params, raw=True)

	# -- plumbing ---------------------------------------------------------

	def _request(self, method, path, params=None, json_body=None, raw=False):
		if not self.is_configured():
			raise errors.ProtectError("Host or API key is not set")
		url = self.base_url() + API_BASE_PATH + path
		if params:
			url += "?" + urlencode(params)
		headers = {
			API_KEY_HEADER: self.api_key,
			"Accept": "application/octet-stream" if raw else "application/json",
		}
		body = None
		if json_body is not None:
			body = json.dumps(json_body).encode("utf-8")
			headers["Content-Type"] = "application/json"
		if self._log is not None:
			# Path only. Camera ids are not secret, but headers and bodies
			# from this class are never logged: one of them is the API key.
			self._log.debug("%s %s", method, API_BASE_PATH + path)
		response = self._transport(
			method, url, headers, body, self.timeout, self.verify_ssl)
		return self._handle(response, raw)

	def _handle(self, response, raw):
		status = response.status
		if status in (401, 403):
			raise errors.ProtectAuthError(
				"API key rejected by the console (HTTP %d)" % status)
		if status == 404:
			raise errors.ProtectNotFoundError("Not found on the console (HTTP 404)")
		if status >= 500:
			raise errors.ProtectUnavailableError(
				"Console reported an internal error (HTTP %d)" % status)
		if status >= 400:
			raise errors.ProtectProtocolError("Request refused (HTTP %d)" % status)
		if raw:
			if not response.body:
				raise errors.ProtectProtocolError("Console returned an empty image")
			return response.body
		if status in _NO_BODY_STATUSES or not response.body:
			return None
		try:
			return json.loads(response.body.decode("utf-8"))
		except (ValueError, UnicodeDecodeError) as exc:
			raise errors.ProtectProtocolError("Could not read the response: %s" % exc)


def _path(value):
	"""Quote one path segment. Camera ids are opaque and come from the console."""
	return quote(str(value), safe="")
