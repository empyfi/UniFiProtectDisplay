# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Letting Protect tell the receiver that something moved.

Why a webhook and not the API
-----------------------------
Protect's Integration API cannot report motion: it has no events endpoint,
its event WebSocket completes the handshake and then delivers nothing, and
the private API the web interface uses rejects an API key outright. All three
were measured against a live console rather than assumed.

Protect's own Alarm Manager, though, can call out: an alarm with trigger
"Motion" and action "Custom Webhook" sends an HTTP request wherever you point
it. So the direction is reversed -- the console calls the receiver, which is
both simpler than a WebSocket client and more reliable, since there is no
connection to keep alive.

The URL carries the camera
--------------------------
Protect sends no payload worth depending on with a GET webhook, so which
camera moved has to be in the URL::

	http://<receiver>:<port>/<token>/motion/<camera-id>

One alarm per camera in Protect, one URL each. The plugin prints the exact
URLs to paste, so no ids are typed by hand.

The token is a random string generated once and kept in the plugin's
settings. It is not a security boundary worth much -- anyone on the LAN who
sees the URL can replay it -- but it does mean a stray request cannot flip
the television to a camera, and a URL that leaks stops working as soon as the
token is regenerated. The listener only ever changes what is on screen; it
cannot be made to do anything else.
"""

import secrets

try:  # pragma: no cover - stdlib layout
	from http.server import BaseHTTPRequestHandler, HTTPServer
	from socketserver import ThreadingMixIn
except ImportError:  # pragma: no cover
	from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
	from SocketServer import ThreadingMixIn

import threading

# Bound on all interfaces: the console has to reach this from elsewhere on
# the network, unlike the TS proxy which deliberately stays on loopback.
BIND_HOST = "0.0.0.0"
DEFAULT_PORT = 8099

MOTION_SEGMENT = "motion"
# A catch-all route used while working out what Protect actually sends. It
# accepts any trailing path, so one URL can be pasted into every alarm.
LEARN_SEGMENT = "learn"

# Captures are written as JSON lines for tools/configure.py to read back.
CAPTURE_PATH = "/tmp/unifiprotectdisplay_webhooks.json"
CAPTURE_LIMIT = 40
TOKEN_BYTES = 9  # 12 characters of url-safe base64


def generate_token():
	"""A fresh URL token. Called once; regenerating invalidates old URLs."""
	return secrets.token_urlsafe(TOKEN_BYTES)


def parse_path(path, token):
	"""Extract the camera id from a webhook path.

	Returns ``None`` for anything that is not a motion call carrying the
	right token, so the handler has one place to decide and the rule can be
	tested without a socket.

	Accepts a trailing slash and a query string, because a hand-edited URL
	in the Protect UI will sooner or later have one.
	"""
	if not path or not token:
		return None
	path = path.split("?", 1)[0].split("#", 1)[0]
	parts = [segment for segment in path.split("/") if segment]
	if len(parts) != 3:
		return None
	given_token, segment, camera_id = parts
	# Compare in constant time: the token is the only thing standing between
	# a stray request and the television changing.
	if not secrets.compare_digest(str(given_token), str(token)):
		return None
	if segment != MOTION_SEGMENT:
		return None
	return camera_id or None


def parse_learn_path(path, token):
	"""True when ``path`` is a learn call carrying the right token.

	Unlike the motion route this accepts anything after the segment, so a
	single URL can go into every alarm while we find out what distinguishes
	them.
	"""
	if not path or not token:
		return False
	path = path.split("?", 1)[0].split("#", 1)[0]
	parts = [segment for segment in path.split("/") if segment]
	if len(parts) < 2:
		return False
	if not secrets.compare_digest(str(parts[0]), str(token)):
		return False
	return parts[1] == LEARN_SEGMENT


def learn_url(host, port, token):
	"""The single URL to paste into every alarm while learning."""
	return "http://%s:%d/%s/%s" % (host, int(port), token, LEARN_SEGMENT)


def motion_url(host, port, token, camera_id):
	"""The URL to paste into Protect for one camera."""
	return "http://%s:%d/%s/%s/%s" % (
		host, int(port), token, MOTION_SEGMENT, camera_id)


class WebhookServer(object):
	"""Listens for Protect's alarm webhooks.

	``on_motion`` is called with the camera id **on a server thread**, so
	whatever it does must be safe there -- the manager hands it straight to
	the task runner, which delivers on enigma2's main loop.
	"""

	def __init__(self, port=DEFAULT_PORT, token=None, on_motion=None,
			log=None, server_factory=None, on_learn=None):
		self.port = int(port or DEFAULT_PORT)
		self.token = token or generate_token()
		self._on_motion = on_motion
		self._on_learn = on_learn
		self._log = log
		self._server_factory = server_factory or _make_server
		self._server = None
		self._thread = None

	@property
	def running(self):
		return self._server is not None

	def start(self):
		"""Bind and serve. Returns the port, or raises if it cannot bind."""
		if self._server is not None:
			return self.port
		self._server = self._server_factory(
			(BIND_HOST, self.port), _handler_for(self))
		self.port = self._server.server_address[1]
		self._thread = threading.Thread(target=self._server.serve_forever)
		self._thread.daemon = True
		self._thread.start()
		self._info("motion webhook listening on port %d", self.port)
		return self.port

	def stop(self):
		server, self._server = self._server, None
		if server is not None:
			try:
				server.shutdown()
				server.server_close()
			except Exception as exc:  # noqa: BLE001
				self._warn("while stopping the webhook listener: %s", exc)
		self._thread = None
		self._info("motion webhook stopped")

	def handle(self, path, method="GET", body=b"", headers=None):
		"""Act on one request. Returns the HTTP status to answer with."""
		if parse_learn_path(path, self.token):
			self._capture(path, method, body, headers)
			return 204
		camera_id = parse_path(path, self.token)
		if camera_id is None:
			# Deliberately terse: an unauthenticated caller learns nothing
			# about what this port is for.
			self._debug("ignored %s %s", method, path)
			return 404
		self._info("motion reported for camera %s", camera_id)
		if self._on_motion is not None:
			try:
				self._on_motion(camera_id)
			except Exception as exc:  # noqa: BLE001
				# A failure here must not turn into an error on Protect's
				# side, or it will start retrying and logging alarms.
				self._warn("motion handler failed: %s", exc)
		return 204

	def _capture(self, path, method, body, headers):
		"""Record a learn call verbatim, for inspection afterwards.

		Written to a file rather than the log because a payload can be long
		and the point is to read it whole, once, while working out what
		Protect sends.
		"""
		import json
		import time
		entry = {
			"at": time.strftime("%Y-%m-%d %H:%M:%S"),
			"method": method,
			"path": path,
			"headers": dict(headers or {}),
			"body": (body or b"").decode("utf-8", "replace"),
		}
		self._info("learn call: %s %s, %d bytes of body",
			method, path, len(body or b""))
		try:
			lines = []
			try:
				with open(CAPTURE_PATH, "r") as handle:
					lines = [line for line in handle.read().splitlines()
						if line.strip()]
			except (OSError, IOError):
				lines = []
			lines.append(json.dumps(entry))
			with open(CAPTURE_PATH, "w") as handle:
				for line in lines[-CAPTURE_LIMIT:]:
					handle.write(line + "\n")
		except (OSError, IOError, ValueError) as exc:
			self._warn("could not record the learn call: %s", exc)
		if self._on_learn is not None:
			try:
				self._on_learn(entry)
			except Exception as exc:  # noqa: BLE001
				self._warn("learn handler failed: %s", exc)

	def _info(self, message, *args):
		if self._log is not None:
			self._log.info(message, *args)

	def _warn(self, message, *args):
		if self._log is not None:
			self._log.error(message, *args)

	def _debug(self, message, *args):
		if self._log is not None:
			self._log.debug(message, *args)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
	daemon_threads = True
	allow_reuse_address = True


def _make_server(address, handler):
	return _ThreadingHTTPServer(address, handler)


def _handler_for(server):
	class _Handler(BaseHTTPRequestHandler):
		protocol_version = "HTTP/1.1"

		def _respond(self, status):
			self.send_response(status)
			self.send_header("Content-Length", "0")
			self.end_headers()

		def do_GET(self):  # noqa: N802 - name fixed by the base class
			self._respond(server.handle(
				self.path, "GET", b"", dict(self.headers)))

		def do_POST(self):  # noqa: N802
			# Protect can be set to POST; the body is not needed, but it has
			# to be read or the connection is left half-consumed.
			try:
				length = int(self.headers.get("Content-Length") or 0)
			except (TypeError, ValueError):
				length = 0
			body = self.rfile.read(length) if length > 0 else b""
			self._respond(server.handle(
				self.path, "POST", body, dict(self.headers)))

		def log_message(self, fmt, *args):
			pass  # the server logs what matters

	return _Handler
