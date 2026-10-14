# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiving motion reports from Protect's Alarm Manager.

This listener is the one part of the plugin reachable from the network, so
what it accepts is spelled out rather than left to the handler's shape.
"""

import socket
import threading

import pytest

from UnifiProtectDisplay import webhook
from UnifiProtectDisplay.webhook import (
	WebhookServer, generate_token, motion_url, parse_path)

TOKEN = "s3cr3t-token"
CAMERA = "64f37aff03330a03e400adef"


class TestTokens:
	def test_tokens_are_url_safe(self):
		token = generate_token()
		assert token and "/" not in token and "?" not in token

	def test_tokens_differ(self):
		assert generate_token() != generate_token()


class TestParsePath:
	def test_accepts_a_well_formed_call(self):
		assert parse_path("/%s/motion/%s" % (TOKEN, CAMERA), TOKEN) == CAMERA

	def test_accepts_a_trailing_slash(self):
		assert parse_path("/%s/motion/%s/" % (TOKEN, CAMERA), TOKEN) == CAMERA

	def test_accepts_a_query_string(self):
		# A URL edited by hand in the Protect UI will eventually have one.
		assert parse_path(
			"/%s/motion/%s?ts=123" % (TOKEN, CAMERA), TOKEN) == CAMERA

	def test_rejects_a_wrong_token(self):
		assert parse_path("/wrong/motion/%s" % CAMERA, TOKEN) is None

	def test_rejects_a_missing_token(self):
		assert parse_path("/motion/%s" % CAMERA, TOKEN) is None

	def test_rejects_another_action(self):
		assert parse_path("/%s/reboot/%s" % (TOKEN, CAMERA), TOKEN) is None

	def test_rejects_a_missing_camera(self):
		assert parse_path("/%s/motion/" % TOKEN, TOKEN) is None

	def test_rejects_extra_segments(self):
		# Nothing should be reachable by burying it deeper in the path.
		assert parse_path(
			"/%s/motion/%s/extra" % (TOKEN, CAMERA), TOKEN) is None

	@pytest.mark.parametrize("path", ["", "/", "/favicon.ico", None])
	def test_rejects_junk(self, path):
		assert parse_path(path, TOKEN) is None

	def test_rejects_when_no_token_is_configured(self):
		assert parse_path("/x/motion/%s" % CAMERA, "") is None


class TestMotionUrl:
	def test_round_trips_through_parse(self):
		url = motion_url("192.168.1.50", 8099, TOKEN, CAMERA)
		path = "/" + url.split("/", 3)[3]
		assert parse_path(path, TOKEN) == CAMERA

	def test_contains_host_and_port(self):
		assert motion_url("10.0.0.2", 9000, TOKEN, CAMERA).startswith(
			"http://10.0.0.2:9000/")


class TestHandle:
	def make(self):
		seen = []
		server = WebhookServer(token=TOKEN, on_motion=seen.append)
		return server, seen

	def test_a_valid_call_reports_the_camera(self):
		server, seen = self.make()
		assert server.handle("/%s/motion/%s" % (TOKEN, CAMERA)) == 204
		assert seen == [CAMERA]

	def test_an_invalid_call_is_a_404_and_reports_nothing(self):
		server, seen = self.make()
		assert server.handle("/nope/motion/%s" % CAMERA) == 404
		assert seen == []

	def test_a_failing_handler_does_not_become_an_error_response(self):
		# Protect retries on failure and logs an alarm; a problem on our
		# side must not turn into noise on theirs.
		def boom(_camera):
			raise RuntimeError("something broke")

		server = WebhookServer(token=TOKEN, on_motion=boom)
		assert server.handle("/%s/motion/%s" % (TOKEN, CAMERA)) == 204

	def test_works_without_a_handler(self):
		server = WebhookServer(token=TOKEN)
		assert server.handle("/%s/motion/%s" % (TOKEN, CAMERA)) == 204


class TestOverTheWire:
	"""What Protect's webhook actually meets."""

	@pytest.fixture
	def server(self):
		seen = []
		srv = WebhookServer(port=0, token=TOKEN, on_motion=seen.append)
		srv.start()
		srv.seen = seen
		yield srv
		srv.stop()

	@staticmethod
	def request(port, path, method="GET", body=b""):
		sock = socket.create_connection(("127.0.0.1", port), timeout=5)
		try:
			head = "%s %s HTTP/1.1\r\nHost: x\r\n" % (method, path)
			if method == "POST":
				head += "Content-Length: %d\r\n" % len(body)
			head += "Connection: close\r\n\r\n"
			sock.sendall(head.encode() + body)
			data = b""
			while True:
				chunk = sock.recv(4096)
				if not chunk:
					break
				data += chunk
			return data
		finally:
			sock.close()

	def test_get_triggers_motion(self, server):
		response = self.request(server.port, "/%s/motion/%s" % (TOKEN, CAMERA))
		assert b" 204 " in response.split(b"\r\n", 1)[0]
		assert server.seen == [CAMERA]

	def test_post_triggers_motion_too(self, server):
		# The Protect dialog offers GET or POST; both must work.
		response = self.request(
			server.port, "/%s/motion/%s" % (TOKEN, CAMERA),
			method="POST", body=b'{"alarm":"motion"}')
		assert b" 204 " in response.split(b"\r\n", 1)[0]
		assert server.seen == [CAMERA]

	def test_a_wrong_token_gets_404(self, server):
		response = self.request(server.port, "/wrong/motion/%s" % CAMERA)
		assert b" 404 " in response.split(b"\r\n", 1)[0]
		assert server.seen == []

	def test_concurrent_calls_are_all_delivered(self, server):
		# Several cameras can report at once.
		threads = [
			threading.Thread(
				target=self.request,
				args=(server.port, "/%s/motion/cam%d" % (TOKEN, i)))
			for i in range(5)]
		for t in threads:
			t.start()
		for t in threads:
			t.join(timeout=10)
		assert sorted(server.seen) == ["cam%d" % i for i in range(5)]


class TestBinding:
	def test_listens_on_all_interfaces(self):
		# Unlike the TS proxy, this one has to be reachable from the
		# console, which is a different machine.
		assert webhook.BIND_HOST == "0.0.0.0"
