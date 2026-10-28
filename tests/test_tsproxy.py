# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""The RTSP-to-MPEG-TS loopback proxy.

Some of these run a real HTTP server on loopback with a fake ffmpeg behind
it, because the contract that matters is the one enigma2's ``eHttpStream``
sees on the wire -- in particular a status line it can parse and a content
type it will not mistake for a playlist.
"""

import io
import socket

import pytest

from UnifiProtectDisplay.player import tsproxy
from UnifiProtectDisplay.player.tsproxy import (
	BIND_HOST, CONTENT_TYPE, TsProxy, default_ffmpeg_args)

RTSP_URL = "rtsps://10.0.0.1:7441/TOK?enableSrtp"


class FakeProcess:
	"""Stands in for an ffmpeg child process."""

	def __init__(self, payload=b"", stderr=b""):
		self.stdout = io.BytesIO(payload)
		self.stderr = io.BytesIO(stderr)
		# The card streamer feeds JPEGs in on stdin, so the stand-in
		# needs one too or it is not standing in for the same thing.
		self.stdin = io.BytesIO()
		self.terminated = False
		self.killed = False
		self._returncode = None

	def poll(self):
		return self._returncode

	def terminate(self):
		self.terminated = True
		self._returncode = 0

	def kill(self):
		self.killed = True
		self._returncode = -9

	def wait(self, timeout=None):
		self._returncode = self._returncode or 0
		return self._returncode


class FakePopen:
	"""Records the command line and hands back a canned process."""

	def __init__(self, payload=b"", stderr=b""):
		self.payload = payload
		self.stderr = stderr
		self.calls = []
		self.process = None

	def __call__(self, args, **kwargs):
		self.calls.append({"args": args, "kwargs": kwargs})
		self.process = FakeProcess(self.payload, self.stderr)
		return self.process


@pytest.fixture
def proxy():
	p = TsProxy(port=0, popen=FakePopen(b"ts-data"))
	p.start()
	yield p
	p.stop()


class TestFfmpegCommand:
	def test_remuxes_without_transcoding(self):
		# -c copy is the point: a set-top box cannot re-encode H.264 live.
		args = default_ffmpeg_args(RTSP_URL)
		assert "-c" in args and args[args.index("-c") + 1] == "copy"

	def test_output_is_mpegts_on_stdout(self):
		# eHttpStream feeds the DVB demux, which needs MPEG-TS.
		args = default_ffmpeg_args(RTSP_URL)
		assert args[args.index("-f") + 1] == "mpegts"
		assert args[-1] == "-"

	def test_uses_tcp_transport(self):
		# UDP RTP loses packets on consumer wifi, and the artefacts are far
		# worse than the small extra latency of TCP.
		args = default_ffmpeg_args(RTSP_URL)
		assert args[args.index("-rtsp_transport") + 1] == "tcp"

	def test_sets_no_rw_timeout(self):
		# The RTSP demuxer in the ffmpeg builds on these images rejects
		# -rw_timeout outright ("Option rw_timeout not found") and exits
		# before reading a byte, which looks exactly like a camera fault.
		# Verified on a GigaBlue UHD Quad 4K Pro, openATV 7.6.
		assert "-rw_timeout" not in default_ffmpeg_args(RTSP_URL)

	def test_takes_the_video_stream_only(self):
		# A UniFi camera offers two audio tracks, one of them Opus, which
		# MPEG-TS cannot carry; ffmpeg may pick it and fail to mux. PiP is
		# muted anyway, so audio would be carried only to be discarded.
		args = default_ffmpeg_args(RTSP_URL)
		assert args[args.index("-map") + 1] == "0:v:0"

	def test_map_comes_after_the_input(self):
		# -map is an output option; before -i ffmpeg rejects it.
		args = default_ffmpeg_args(RTSP_URL)
		assert args.index("-map") > args.index("-i")

	def test_url_is_passed_as_the_input(self):
		args = default_ffmpeg_args(RTSP_URL)
		assert args[args.index("-i") + 1] == RTSP_URL

	def test_binary_path_is_configurable(self):
		assert default_ffmpeg_args(RTSP_URL, "/opt/bin/ffmpeg")[0] == "/opt/bin/ffmpeg"


class TestRegistration:
	def test_register_returns_a_loopback_url(self, proxy):
		url = proxy.register("cam1", RTSP_URL)
		assert url.startswith("http://%s:%d/cam/" % (BIND_HOST, proxy.port))

	def test_path_token_is_not_the_camera_id(self, proxy):
		# A URL that leaks into a log or a bouquet should not be a durable
		# handle on the camera.
		url = proxy.register("cam1", RTSP_URL)
		assert "cam1" not in url.rsplit("/", 1)[1]

	def test_tokens_differ_between_cameras(self, proxy):
		a = proxy.register("cam1", RTSP_URL)
		b = proxy.register("cam2", RTSP_URL)
		assert a != b

	def test_reregistering_keeps_the_token(self, proxy):
		# A quality change must not invalidate a reference enigma2 holds.
		first = proxy.register("cam1", RTSP_URL)
		second = proxy.register("cam1", "rtsp://other/url")
		assert first == second

	def test_reregistering_updates_the_source_url(self, proxy):
		proxy.register("cam1", RTSP_URL)
		proxy.register("cam1", "rtsp://other/url")
		token = proxy.url_for("cam1").rsplit("/", 1)[1]
		assert proxy.source_for_token(token).url == "rtsp://other/url"

	def test_url_for_unknown_camera_is_none(self, proxy):
		assert proxy.url_for("nope") is None

	def test_register_before_start_is_refused(self):
		# Silently returning a dead URL would surface much later as a black
		# PiP window with nothing in the log.
		with pytest.raises(RuntimeError):
			TsProxy().register("cam1", RTSP_URL)


class TestLifecycle:
	def test_start_picks_a_free_port(self):
		p = TsProxy(port=0, popen=FakePopen())
		try:
			assert p.start() > 0
			assert p.running
		finally:
			p.stop()

	def test_start_is_idempotent(self):
		p = TsProxy(port=0, popen=FakePopen())
		try:
			assert p.start() == p.start()
		finally:
			p.stop()

	def test_stop_clears_registrations(self):
		p = TsProxy(port=0, popen=FakePopen())
		p.start()
		p.register("cam1", RTSP_URL)
		p.stop()
		assert not p.running
		assert p.url_for("cam1") is None

	def test_stop_is_safe_when_never_started(self):
		TsProxy().stop()


class TestStreaming:
	def test_pumps_process_output_to_the_writer(self, proxy):
		popen = FakePopen(b"A" * 1000)
		proxy._popen = popen
		proxy.register("cam1", RTSP_URL)
		token = proxy.url_for("cam1").rsplit("/", 1)[1]
		chunks = []
		written = proxy.stream(proxy.source_for_token(token), chunks.append)
		assert b"".join(chunks) == b"A" * 1000
		assert written == 1000

	def test_process_is_always_reaped(self, proxy):
		# A leaked ffmpeg keeps pulling from the camera; with four of them
		# that is real CPU and real bandwidth.
		popen = FakePopen(b"data")
		proxy._popen = popen
		proxy.register("cam1", RTSP_URL)
		token = proxy.url_for("cam1").rsplit("/", 1)[1]
		proxy.stream(proxy.source_for_token(token), lambda c: None)
		assert popen.process.terminated

	def test_client_disconnect_ends_cleanly(self, proxy):
		# A broken pipe is how this normally ends, not an error.
		popen = FakePopen(b"B" * 10000)
		proxy._popen = popen
		proxy.register("cam1", RTSP_URL)
		token = proxy.url_for("cam1").rsplit("/", 1)[1]

		def explode(_chunk):
			raise IOError("broken pipe")

		proxy.stream(proxy.source_for_token(token), explode)
		assert popen.process.terminated

	def test_should_continue_false_stops_the_pump(self, proxy):
		popen = FakePopen(b"C" * 10000)
		proxy._popen = popen
		proxy.register("cam1", RTSP_URL)
		token = proxy.url_for("cam1").rsplit("/", 1)[1]
		written = proxy.stream(
			proxy.source_for_token(token), lambda c: None,
			should_continue=lambda: False)
		assert written == 0

	def test_ffmpeg_stderr_is_surfaced(self):
		# Without this a failing camera is a black corner and an empty log.
		logged = []

		class Log:
			def info(self, message, *args):
				pass

			def error(self, message, *args):
				logged.append(message % args if args else message)

		p = TsProxy(port=0, popen=FakePopen(b"", b"Connection refused"), log=Log())
		p.start()
		try:
			p.register("cam1", RTSP_URL)
			token = p.url_for("cam1").rsplit("/", 1)[1]
			p.stream(p.source_for_token(token), lambda c: None)
		finally:
			p.stop()
		assert any("Connection refused" in line for line in logged)


class TestHttpContract:
	"""What eHttpStream actually sees. See lib/base/httpstream.cpp."""

	@staticmethod
	def raw_get(port, path):
		sock = socket.create_connection((BIND_HOST, port), timeout=5)
		try:
			sock.sendall(
				("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n"
					% (path, BIND_HOST)).encode())
			data = b""
			while True:
				chunk = sock.recv(65536)
				if not chunk:
					break
				data += chunk
			return data
		finally:
			sock.close()

	def test_status_line_has_three_tokens(self, proxy):
		# eHttpStream parses it with sscanf("%99s %d %99s") and requires all
		# three; a two-token status line is rejected outright.
		url = proxy.register("cam1", RTSP_URL)
		response = self.raw_get(proxy.port, "/cam/" + url.rsplit("/", 1)[1])
		status_line = response.split(b"\r\n", 1)[0].decode()
		assert len(status_line.split()) == 3
		assert status_line.split()[1] == "200"

	def test_content_type_is_not_mistaken_for_a_playlist(self, proxy):
		# eHttpStream treats application/text, audio/x-mpegurl, audio/mpegurl
		# and application/m3u as playlists and tries to read a URL from the
		# body instead of demuxing it.
		url = proxy.register("cam1", RTSP_URL)
		response = self.raw_get(proxy.port, "/cam/" + url.rsplit("/", 1)[1])
		headers = response.split(b"\r\n\r\n", 1)[0].decode().lower()
		assert ("content-type: " + CONTENT_TYPE) in headers
		for playlist_type in ("application/text", "audio/x-mpegurl",
				"audio/mpegurl", "application/m3u"):
			assert playlist_type not in headers

	def test_body_is_the_remuxed_stream(self, proxy):
		url = proxy.register("cam1", RTSP_URL)
		response = self.raw_get(proxy.port, "/cam/" + url.rsplit("/", 1)[1])
		assert response.split(b"\r\n\r\n", 1)[1] == b"ts-data"

	def test_unknown_token_is_404(self, proxy):
		response = self.raw_get(proxy.port, "/cam/not-a-real-token")
		assert b" 404 " in response.split(b"\r\n", 1)[0]

	def test_other_paths_are_404(self, proxy):
		response = self.raw_get(proxy.port, "/")
		assert b" 404 " in response.split(b"\r\n", 1)[0]


class TestOfflineComesFirst:
	"""An offline camera must not cost a Protect round trip to discover.

	Measured on a receiver before this was separated: zapping to a camera
	the console had already reported unreachable sat black for six to
	eleven seconds -- the time spent asking Protect for a stream URL that
	was never going to work -- and only then showed the card. Long enough
	that the channel reads as broken, which is the one thing the card is
	there to prevent.
	"""

	CARD = b"\xff\xd8card"

	@staticmethod
	def make(popen, **kwargs):
		proxy = TsProxy(
			popen=popen, args_builder=lambda url, ffmpeg: ["x"], **kwargs)
		proxy.start()
		return proxy

	@staticmethod
	def status(response):
		return response.split(b"\r\n", 1)[0]

	@staticmethod
	def body(response):
		return response.split(b"\r\n\r\n", 1)[1]

	def test_camera_for_token_resolves_without_the_api(self):
		calls = []
		proxy = self.make(
			FakePopen(payload=b"ts"),
			url_resolver=lambda token: calls.append(token))
		try:
			proxy.register_token("cam1", "tok1")
			assert proxy.camera_for_token("tok1") == "cam1"
			assert calls == []
		finally:
			proxy.stop()

	def test_camera_for_an_unknown_token_is_none(self):
		proxy = self.make(FakePopen(payload=b"ts"))
		try:
			assert proxy.camera_for_token("nope") is None
		finally:
			proxy.stop()

	def test_stop_forgets_the_token_mapping_too(self):
		proxy = self.make(FakePopen(payload=b"ts"))
		proxy.register_token("cam1", "tok1")
		proxy.stop()
		assert proxy.camera_for_token("tok1") is None

	def test_an_offline_camera_is_never_resolved(self):
		resolved = []
		proxy = self.make(
			FakePopen(payload=b"ts"),
			url_resolver=lambda token: resolved.append(token) or (
				"cam1", RTSP_URL),
			offline_card=lambda camera_id: self.CARD)
		try:
			proxy.register_token("cam1", "tok1")
			response = TestHttpContract.raw_get(proxy.port, "/cam/tok1")
			assert b" 200 " in self.status(response)
			assert resolved == []
		finally:
			proxy.stop()

	def test_a_connected_camera_is_still_resolved(self):
		resolved = []
		proxy = self.make(
			FakePopen(payload=b"ts-data"),
			url_resolver=lambda token: resolved.append(token) or (
				"cam1", RTSP_URL),
			offline_card=lambda camera_id: None)
		try:
			proxy.register_token("cam1", "tok1")
			response = TestHttpContract.raw_get(proxy.port, "/cam/tok1")
			assert self.body(response) == b"ts-data"
			assert resolved == ["tok1"]
		finally:
			proxy.stop()

	def test_a_camera_that_goes_offline_during_the_lookup_gets_a_card(self):
		# The console can drop a camera between the two checks. Rare, but
		# the second check costs nothing and the failure mode it prevents
		# is a dead channel.
		#
		# Both paths end at the same fake process, so the payload says
		# nothing; the command line does. A remux is the args_builder
		# injected above, a card is the encoder in player.imagestream.
		states = iter([None, self.CARD])
		popen = FakePopen(payload=b"ts-data")
		proxy = self.make(
			popen,
			url_resolver=lambda token: ("cam1", RTSP_URL),
			offline_card=lambda camera_id: next(states, self.CARD))
		try:
			proxy.register_token("cam1", "tok1")
			TestHttpContract.raw_get(proxy.port, "/cam/tok1")
			assert popen.calls, "nothing was started at all"
			started = popen.calls[-1]["args"]
			assert "libx264" in started
			assert started != ["x"]
		finally:
			proxy.stop()

	def test_a_connected_camera_runs_the_remux_not_the_encoder(self):
		# The other side of the same coin: a camera that is up must not be
		# quietly turned into a slideshow.
		popen = FakePopen(payload=b"ts-data")
		proxy = self.make(
			popen,
			url_resolver=lambda token: ("cam1", RTSP_URL),
			offline_card=lambda camera_id: None)
		try:
			proxy.register_token("cam1", "tok1")
			TestHttpContract.raw_get(proxy.port, "/cam/tok1")
			assert popen.calls[-1]["args"] == ["x"]
		finally:
			proxy.stop()

	def test_a_stream_that_cannot_be_resolved_still_plays_something(self):
		# An unreachable console, or a tier that will not enable. Without a
		# card this is a 404, which enigma2 shows as a channel that simply
		# does nothing.
		proxy = self.make(
			FakePopen(payload=b"ts"),
			url_resolver=lambda token: None,
			offline_card=lambda camera_id: None,
			problem_card=lambda camera_id: self.CARD)
		try:
			proxy.register_token("cam1", "tok1")
			response = TestHttpContract.raw_get(proxy.port, "/cam/tok1")
			assert b" 200 " in self.status(response)
		finally:
			proxy.stop()

	def test_a_remux_that_produces_nothing_falls_back_to_a_card(self):
		# The console listed the camera, the URL resolved, and ffmpeg then
		# read not one byte -- a camera gone since the list was taken. The
		# response is already open by then, so the card has to go into the
		# same one or the channel stays blank.
		popen = FakePopen(payload=b"")
		proxy = self.make(
			popen,
			url_resolver=lambda token: ("cam1", RTSP_URL),
			offline_card=lambda camera_id: None,
			problem_card=lambda camera_id: self.CARD)
		try:
			proxy.register_token("cam1", "tok1")
			TestHttpContract.raw_get(proxy.port, "/cam/tok1")
			started = [call["args"] for call in popen.calls]
			assert started[0] == ["x"], "the remux should be tried first"
			assert any("libx264" in args for args in started[1:]), (
				"nothing fell back to a card")
		finally:
			proxy.stop()

	def test_a_remux_that_produces_nothing_and_has_no_card_just_ends(self):
		popen = FakePopen(payload=b"")
		proxy = self.make(
			popen,
			url_resolver=lambda token: ("cam1", RTSP_URL),
			offline_card=lambda camera_id: None)
		try:
			proxy.register_token("cam1", "tok1")
			response = TestHttpContract.raw_get(proxy.port, "/cam/tok1")
			assert b" 200 " in self.status(response)
			assert [call["args"] for call in popen.calls] == [["x"]]
		finally:
			proxy.stop()

	def test_without_a_problem_card_it_is_still_a_404(self):
		proxy = self.make(
			FakePopen(payload=b"ts"),
			url_resolver=lambda token: None,
			offline_card=lambda camera_id: None)
		try:
			proxy.register_token("cam1", "tok1")
			response = TestHttpContract.raw_get(proxy.port, "/cam/tok1")
			assert b" 404 " in self.status(response)
		finally:
			proxy.stop()


class TestCardRoutes:
	"""The routes that serve a picture rather than a camera."""

	@staticmethod
	def make(popen, **kwargs):
		proxy = TsProxy(
			popen=popen, args_builder=lambda url, ffmpeg: ["x"], **kwargs)
		proxy.start()
		return proxy

	def test_the_slides_route_serves_a_stream(self):
		proxy = self.make(
			FakePopen(payload=b"ts"),
			slides_source=lambda: b"\xff\xd8slide")
		try:
			response = TestHttpContract.raw_get(proxy.port, "/slides")
			assert b" 200 " in response.split(b"\r\n", 1)[0]
		finally:
			proxy.stop()

	def test_the_slides_route_is_404_without_a_source(self):
		proxy = self.make(FakePopen(payload=b"ts"))
		try:
			response = TestHttpContract.raw_get(proxy.port, "/slides")
			assert b" 404 " in response.split(b"\r\n", 1)[0]
		finally:
			proxy.stop()

	def test_slides_run_faster_than_the_cards(self):
		# The priming cushion is frames divided by frame rate, so the
		# frame rate is what decides how far behind the world a camera
		# you are actually watching ends up.
		popen = FakePopen(payload=b"ts")
		proxy = self.make(popen, slides_source=lambda: b"\xff\xd8slide")
		try:
			TestHttpContract.raw_get(proxy.port, "/slides")
			args = popen.calls[-1]["args"]
			rate = int(args[args.index("-framerate") + 1])
			assert rate == tsproxy.SLIDES_FRAMERATE
			assert rate > 2
		finally:
			proxy.stop()

	def test_the_grid_keeps_the_card_frame_rate(self):
		popen = FakePopen(payload=b"ts")
		proxy = self.make(popen, grid_source=lambda: b"\xff\xd8grid")
		try:
			TestHttpContract.raw_get(proxy.port, "/grid")
			args = popen.calls[-1]["args"]
			assert int(args[args.index("-framerate") + 1]) == 2
		finally:
			proxy.stop()


class TestBinding:
	def test_binds_to_loopback_only(self, proxy):
		# An unauthenticated camera feed must not appear on the LAN because
		# a plugin needed an IPC mechanism.
		assert proxy._server.server_address[0] == "127.0.0.1"
		assert tsproxy.BIND_HOST == "127.0.0.1"
