# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turning an RTSP camera into something enigma2's PiP can play.

Why this exists
---------------
PiP calls ``setTarget(1)`` on the service it is given. Only
``eDVBServicePlay`` implements it; ``eServiceMP3`` (GStreamer, 4097) and
``eServiceApp`` (exteplayer3, 5002) both ``return -1``, so neither can ever
occupy the second decoder. The DVB service path can read a stream, but it
reads it through ``eHttpStream``, which speaks HTTP and expects MPEG-TS.

So the gap between "camera speaks RTSP" and "PiP reads HTTP MPEG-TS" is
closed here: ffmpeg remuxes, and a loopback HTTP server hands the result to
enigma2. ``-c copy`` means no transcoding -- the video is repackaged, not
re-encoded, so the cost is a few percent of one core rather than an
impossible real-time H.264 encode on a set-top box.

Security
--------
The server binds to 127.0.0.1 only: an unauthenticated camera feed must not
appear on the LAN because a plugin needed an IPC mechanism. Each camera also
gets a random path token per session, so a URL that ends up in a log or a
bouquet file stops working at the next restart rather than remaining a usable
handle on the camera.
"""

import secrets
import subprocess
import threading

try:  # pragma: no cover - stdlib layout differs only on ancient images
	from http.server import BaseHTTPRequestHandler, HTTPServer
	from socketserver import ThreadingMixIn
except ImportError:  # pragma: no cover
	from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
	from SocketServer import ThreadingMixIn

BIND_HOST = "127.0.0.1"
PATH_PREFIX = "/cam/"

# eHttpStream treats a handful of content types as playlists and then tries to
# read a URL out of the body (lib/base/httpstream.cpp). video/mp2t is not one
# of them, which is exactly what we need.
CONTENT_TYPE = "video/mp2t"

# How much we move per read. One TS packet is 188 bytes; a multiple of that
# keeps packets whole and avoids splitting one across two socket writes.
CHUNK_SIZE = 188 * 350  # ~64 KiB


def default_ffmpeg_args(url, ffmpeg="ffmpeg"):
	"""Command line for remuxing ``url`` to MPEG-TS on stdout.

	Every option here is deliberate, and two of them were learned the hard
	way on a real receiver:

	``-c copy`` is the point of the whole exercise: repackage, never
	re-encode, because a set-top box cannot encode H.264 in real time.

	``-rtsp_transport tcp`` because UDP RTP loses packets on consumer wifi,
	and the artefacts are far worse than the slight extra latency.

	``-map 0:v:0`` takes the video and nothing else. A UniFi camera offers
	two audio tracks, one of them Opus, which MPEG-TS cannot carry -- left
	to its own devices ffmpeg may pick it and fail to mux. PiP is started
	muted anyway (``setTarget(1, True)``), so audio would be carried only to
	be thrown away.

	No read timeout is set. ``-rw_timeout`` is rejected outright by the
	RTSP demuxer in the ffmpeg builds shipped on these images -- "Option
	rw_timeout not found" -- and ffmpeg then exits before reading a single
	byte, which shows up as a black corner with no other symptom. A
	stalled camera is handled by stopping the process when the client
	disconnects instead, which costs nothing and cannot misfire.
	"""
	return [
		ffmpeg,
		"-hide_banner",
		"-loglevel", "error",
		"-rtsp_transport", "tcp",
		"-i", url,
		"-map", "0:v:0",
		"-c", "copy",
		"-f", "mpegts",
		"-muxdelay", "0",
		"-",
	]


class _Source(object):
	__slots__ = ("camera_id", "url", "token")

	def __init__(self, camera_id, url, token):
		self.camera_id = camera_id
		self.url = url
		self.token = token


class TsProxy(object):
	"""Loopback HTTP server that remuxes registered RTSP URLs on demand.

	Lazy by design: ffmpeg starts when enigma2 opens the connection and dies
	when it closes it. Nothing runs while the corner is off, and stopping PiP
	is enough to stop the camera traffic -- there is no separate teardown
	that can be forgotten.
	"""

	def __init__(self, ffmpeg_path="ffmpeg", port=0, log=None,
			popen=None, args_builder=None, server_factory=None,
			url_resolver=None):
		self.ffmpeg_path = ffmpeg_path
		self.requested_port = port
		self.port = None
		self._log = log
		self._popen = popen or subprocess.Popen
		self._args_builder = args_builder or default_ffmpeg_args
		self._server_factory = server_factory or _make_server
		# Called with a token when it is not registered: lets bouquet
		# entries exist before any camera URL has been fetched, so the
		# receiver does not make a Protect request per camera at boot.
		# Runs on a server thread, so blocking there is fine.
		self._url_resolver = url_resolver
		self._sources = {}  # token -> _Source
		self._by_camera = {}  # camera_id -> token
		self._server = None
		self._thread = None
		self._lock = threading.Lock()

	# -- lifecycle --------------------------------------------------------

	def start(self):
		"""Bind and serve. Returns the port actually in use.

		Port 0 asks the OS for a free one, which avoids colliding with the
		streaming proxy, the web interface, or another plugin.
		"""
		if self._server is not None:
			return self.port
		self._server = self._server_factory(
			(BIND_HOST, self.requested_port), _handler_for(self))
		self.port = self._server.server_address[1]
		self._thread = threading.Thread(target=self._server.serve_forever)
		self._thread.daemon = True
		self._thread.start()
		self._info("TS proxy listening on %s:%s", BIND_HOST, self.port)
		return self.port

	def stop(self):
		"""Shut the server down and forget every registered source."""
		server, self._server = self._server, None
		if server is not None:
			try:
				server.shutdown()
				server.server_close()
			except Exception as exc:  # noqa: BLE001
				self._warn("while stopping TS proxy: %s", exc)
		self._thread = None
		self.port = None
		with self._lock:
			self._sources.clear()
			self._by_camera.clear()
		self._info("TS proxy stopped")

	@property
	def running(self):
		return self._server is not None

	# -- sources ----------------------------------------------------------

	def register(self, camera_id, rtsp_url):
		"""Make ``rtsp_url`` reachable, and return the local HTTP URL for it.

		Re-registering a camera keeps its token, so a quality change does not
		invalidate a reference enigma2 may still hold.
		"""
		if not self.running:
			raise RuntimeError("TS proxy is not running")
		with self._lock:
			token = self._by_camera.get(camera_id)
			if token is None:
				# token_urlsafe, not the camera id: see the module docstring.
				token = secrets.token_urlsafe(12)
				self._by_camera[camera_id] = token
			self._sources[token] = _Source(camera_id, rtsp_url, token)
		return self.url_for(camera_id)

	def url_for(self, camera_id):
		"""Loopback URL for a registered camera, or ``None``."""
		token = self._by_camera.get(camera_id)
		if token is None or not self.running:
			return None
		return "http://%s:%d%s%s" % (BIND_HOST, self.port, PATH_PREFIX, token)

	def source_for_token(self, token):
		with self._lock:
			source = self._sources.get(token)
		if source is not None:
			return source
		if self._url_resolver is None:
			return None
		try:
			resolved = self._url_resolver(token)
		except Exception as exc:  # noqa: BLE001
			self._warn("could not resolve %s: %s", token, exc)
			return None
		if not resolved:
			return None
		camera_id, url = resolved
		with self._lock:
			source = _Source(camera_id, url, token)
			self._sources[token] = source
			self._by_camera[camera_id] = token
		return source

	def register_token(self, camera_id, token, rtsp_url=None):
		"""Bind a fixed token, with the URL resolved later if not given."""
		with self._lock:
			self._by_camera[camera_id] = token
			if rtsp_url:
				self._sources[token] = _Source(camera_id, rtsp_url, token)
			else:
				self._sources.pop(token, None)
		return self.url_for(camera_id)

	def forget(self, camera_id):
		"""Drop a cached URL so the next request resolves it again."""
		with self._lock:
			token = self._by_camera.get(camera_id)
			if token:
				self._sources.pop(token, None)

	# -- streaming --------------------------------------------------------

	def stream(self, source, write, should_continue=None):
		"""Pump remuxed TS from ffmpeg into ``write`` until the client leaves.

		Returns the number of bytes written. Always reaps the process: a
		leaked ffmpeg would keep pulling from the camera, and on a box with
		four of them that is real CPU and real bandwidth.
		"""
		args = self._args_builder(source.url, self.ffmpeg_path)
		self._info("starting remux for camera %s", source.camera_id)
		process = self._popen(
			args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
			# ffmpeg must never inherit a terminal; on some images it would
			# otherwise try to read from stdin and stall.
			stdin=subprocess.DEVNULL,
			close_fds=True)
		written = 0
		# read1() returns as soon as anything is available; read() would block
		# until the whole buffer is full, which on a low-bitrate camera means
		# holding back about a second of video before forwarding any of it.
		# That latency is exactly what a surveillance corner must not have.
		reader = getattr(process.stdout, "read1", None) or process.stdout.read
		try:
			while True:
				if should_continue is not None and not should_continue():
					break
				chunk = reader(CHUNK_SIZE)
				if not chunk:
					break
				write(chunk)
				written += len(chunk)
		except (IOError, OSError, ValueError):
			# Broken pipe: enigma2 closed the connection, which is the normal
			# way this ends. Not worth an error line.
			pass
		finally:
			self._terminate(process, source)
		return written

	def _terminate(self, process, source):
		try:
			if process.poll() is None:
				process.terminate()
				try:
					process.wait(timeout=3)
				except Exception:  # noqa: BLE001 - old Python has no timeout
					process.kill()
			# Surface why ffmpeg gave up; without this a failing camera looks
			# like a black corner with nothing in the log.
			stderr = b""
			if process.stderr is not None:
				try:
					stderr = process.stderr.read() or b""
				except (IOError, OSError, ValueError):
					stderr = b""
			if stderr:
				self._warn("ffmpeg (camera %s): %s", source.camera_id,
					stderr.decode("utf-8", "replace").strip()[:400])
		except Exception as exc:  # noqa: BLE001
			self._warn("while stopping ffmpeg: %s", exc)
		finally:
			for stream in (process.stdout, process.stderr):
				try:
					if stream is not None:
						stream.close()
				except (IOError, OSError):
					pass

	# -- logging ----------------------------------------------------------

	def _info(self, message, *args):
		if self._log is not None:
			self._log.info(message, *args)

	def _warn(self, message, *args):
		if self._log is not None:
			self._log.error(message, *args)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
	daemon_threads = True
	# enigma2 reconnects immediately when PiP restarts; without this the
	# rebind fails with "address already in use" during TIME_WAIT.
	allow_reuse_address = True


def _make_server(address, handler):
	return _ThreadingHTTPServer(address, handler)


def _handler_for(proxy):
	"""Build a handler class bound to one proxy instance."""

	class _Handler(BaseHTTPRequestHandler):
		# HTTP/1.0 so the response is delimited by closing the connection.
		# A live stream has no Content-Length, and enigma2's eHttpStream does
		# not need chunked encoding.
		protocol_version = "HTTP/1.0"

		def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
			path = self.path.split("?", 1)[0]
			if not path.startswith(PATH_PREFIX):
				self.send_error(404, "Not found")
				return
			token = path[len(PATH_PREFIX):]
			source = proxy.source_for_token(token)
			if source is None:
				self.send_error(404, "Unknown stream")
				return
			self.send_response(200)
			self.send_header("Content-Type", CONTENT_TYPE)
			self.send_header("Cache-Control", "no-store")
			self.end_headers()
			proxy.stream(source, self.wfile.write)

		def log_message(self, fmt, *args):
			# BaseHTTPRequestHandler logs every request to stderr, which on a
			# receiver means the enigma2 log. The proxy logs what matters.
			pass

	return _Handler
