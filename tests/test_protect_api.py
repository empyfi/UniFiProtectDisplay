# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Protect Integration API client and its data models.

Everything runs against a fake transport, so these tests describe the contract
the plugin expects from a console without needing one.
"""

import json

import pytest

from UnifiProtectDisplay.protect import errors
from UnifiProtectDisplay.protect.api import API_KEY_HEADER, ProtectApi, Response
from UnifiProtectDisplay.protect.models import (
	Camera, StreamSet, to_plain_rtsp, QUALITY_HIGH, QUALITY_LOW, QUALITY_MEDIUM)

RTSPS = "rtsps://10.0.0.1:7441/TOKEN123?enableSrtp"


class FakeTransport:
	"""Records calls and replays queued responses."""

	def __init__(self, *responses):
		self.responses = list(responses)
		self.calls = []

	def __call__(self, method, url, headers, body, timeout, verify_ssl):
		self.calls.append({
			"method": method, "url": url, "headers": headers,
			"body": body, "timeout": timeout, "verify_ssl": verify_ssl,
		})
		if not self.responses:
			return Response(200, b"{}")
		result = self.responses.pop(0)
		if isinstance(result, Exception):
			raise result
		return result


def json_response(payload, status=200):
	return Response(status, json.dumps(payload).encode("utf-8"))


def make_api(*responses, **kwargs):
	transport = FakeTransport(*responses)
	api = ProtectApi("10.0.0.1", "secret-key", transport=transport, **kwargs)
	return api, transport


class TestConfiguration:
	def test_bare_host_gets_https(self):
		# Protect serves nothing over plain HTTP; a user typing an IP should
		# not have to know that.
		api, _ = make_api()
		assert api.base_url() == "https://10.0.0.1"

	def test_explicit_scheme_is_kept(self):
		api = ProtectApi("http://10.0.0.1", "k")
		assert api.base_url() == "http://10.0.0.1"

	def test_trailing_slash_is_trimmed(self):
		api = ProtectApi("https://10.0.0.1/", "k")
		assert api.base_url() == "https://10.0.0.1"

	def test_is_configured_requires_both_host_and_key(self):
		assert not ProtectApi("", "k").is_configured()
		assert not ProtectApi("h", "").is_configured()
		assert ProtectApi("h", "k").is_configured()

	def test_unconfigured_client_refuses_to_call(self):
		with pytest.raises(errors.ProtectError):
			ProtectApi("", "").meta_info()


class TestRequestShape:
	def test_integration_base_path_and_api_key_header(self):
		api, transport = make_api(json_response({"applicationVersion": "6.0.0"}))
		api.meta_info()
		call = transport.calls[0]
		assert call["url"] == (
			"https://10.0.0.1/proxy/protect/integration/v1/meta/info")
		assert call["headers"][API_KEY_HEADER] == "secret-key"

	def test_tls_verification_is_off_by_default(self):
		# A UniFi console presents a self-signed certificate; with
		# verification on, every call fails.
		api, transport = make_api(json_response({}))
		api.meta_info()
		assert transport.calls[0]["verify_ssl"] is False

	def test_tls_verification_can_be_turned_on(self):
		api, transport = make_api(json_response({}), verify_ssl=True)
		api.meta_info()
		assert transport.calls[0]["verify_ssl"] is True

	def test_camera_id_is_path_quoted(self):
		api, transport = make_api(json_response({}))
		api.get_rtsps_streams("a/b c")
		assert "a%2Fb%20c" in transport.calls[0]["url"]

	def test_post_sends_json_and_keeps_its_method(self):
		api, transport = make_api(json_response({}))
		api.create_rtsps_streams("cam1", "high")
		call = transport.calls[0]
		assert call["method"] == "POST"
		assert json.loads(call["body"].decode()) == {"qualities": ["high"]}
		assert call["headers"]["Content-Type"] == "application/json"


class TestCameraList:
	def test_parses_cameras(self):
		api, _ = make_api(json_response([
			{"id": "1", "name": "Hof", "state": "CONNECTED"},
			{"id": "2", "name": "Tor", "state": "DISCONNECTED"},
		]))
		cameras = api.list_cameras()
		assert [c.name for c in cameras] == ["Hof", "Tor"]
		assert cameras[0].connected is True
		assert cameras[1].connected is False

	def test_entry_without_id_is_skipped_not_fatal(self):
		# One malformed device must not cost the user the whole list.
		api, _ = make_api(json_response([{"name": "broken"}, {"id": "2"}]))
		assert [c.id for c in api.list_cameras()] == ["2"]

	def test_unknown_fields_are_tolerated(self):
		# Protect adds fields between releases.
		api, _ = make_api(json_response([
			{"id": "1", "name": "Hof", "somethingNew": {"deep": 1}}]))
		assert api.list_cameras()[0].name == "Hof"

	def test_non_list_payload_is_a_protocol_error(self):
		api, _ = make_api(json_response({"unexpected": True}))
		with pytest.raises(errors.ProtectProtocolError):
			api.list_cameras()


class TestStreams:
	def test_parses_quality_map(self):
		api, _ = make_api(json_response({"high": RTSPS, "low": None}))
		streams = api.get_rtsps_streams("cam1")
		assert streams.available() == ["high"]
		assert streams.url_for(QUALITY_HIGH) == RTSPS

	def test_empty_map_means_rtsp_is_off(self):
		# Protect disables RTSP per camera by default.
		api, _ = make_api(json_response({}))
		assert len(api.get_rtsps_streams("cam1")) == 0


class TestSnapshot:
	def test_returns_raw_bytes(self):
		api, _ = make_api(Response(200, b"\xff\xd8jpegdata"))
		assert api.get_snapshot("cam1") == b"\xff\xd8jpegdata"

	def test_high_quality_flag_is_sent(self):
		api, transport = make_api(Response(200, b"x"))
		api.get_snapshot("cam1", high_quality=True)
		assert "highQuality=true" in transport.calls[0]["url"]

	def test_low_quality_is_the_default(self):
		# The picture is scaled into a corner anyway; a 4K JPEG would cost
		# console time and decode time for nothing.
		api, transport = make_api(Response(200, b"x"))
		api.get_snapshot("cam1")
		assert "highQuality=false" in transport.calls[0]["url"]

	def test_empty_body_is_a_protocol_error(self):
		api, _ = make_api(Response(200, b""))
		with pytest.raises(errors.ProtectProtocolError):
			api.get_snapshot("cam1")


class TestErrorMapping:
	@pytest.mark.parametrize("status", [401, 403])
	def test_rejected_key_raises_auth_error(self, status):
		api, _ = make_api(Response(status, b""))
		with pytest.raises(errors.ProtectAuthError):
			api.meta_info()

	def test_missing_camera_raises_not_found(self):
		api, _ = make_api(Response(404, b""))
		with pytest.raises(errors.ProtectNotFoundError):
			api.get_rtsps_streams("gone")

	@pytest.mark.parametrize("status", [500, 503])
	def test_server_error_is_retryable(self, status):
		# HA has seen 503 from the snapshot endpoint under load; callers key
		# their retry behaviour off this type.
		api, _ = make_api(Response(status, b""))
		with pytest.raises(errors.ProtectUnavailableError):
			api.meta_info()

	def test_other_4xx_is_a_protocol_error(self):
		api, _ = make_api(Response(422, b""))
		with pytest.raises(errors.ProtectProtocolError):
			api.meta_info()

	def test_malformed_json_is_a_protocol_error(self):
		api, _ = make_api(Response(200, b"<html>not json</html>"))
		with pytest.raises(errors.ProtectProtocolError):
			api.meta_info()

	def test_transport_failure_propagates_as_unavailable(self):
		api, _ = make_api(errors.ProtectUnavailableError("no route to host"))
		with pytest.raises(errors.ProtectUnavailableError):
			api.meta_info()

	def test_204_returns_none_rather_than_failing(self):
		api, _ = make_api(Response(204, b""))
		assert api.meta_info() is None


class TestSecretsAreNotLogged:
	def test_log_never_receives_the_api_key(self):
		lines = []

		class Log:
			def debug(self, message, *args):
				lines.append(message % args if args else message)

			def info(self, message, *args):
				self.debug(message, *args)

			def error(self, message, *args):
				self.debug(message, *args)

		api, _ = make_api(json_response({}), log=Log())
		api.meta_info()
		# Enigma2 logs get posted in support forums; an API key in one is a
		# disclosed credential.
		assert lines
		assert not any("secret-key" in line for line in lines)


class TestCameraModel:
	def test_name_falls_back_to_id(self):
		assert Camera.from_json({"id": "abc"}).name == "abc"

	def test_is_connected_boolean_is_honoured(self):
		assert Camera.from_json({"id": "1", "isConnected": False}).connected is False

	def test_missing_state_assumes_usable(self):
		# Never hide a camera because of a field we may be misreading.
		assert Camera.from_json({"id": "1"}).connected is True

	def test_non_dict_entry_returns_none(self):
		assert Camera.from_json("nonsense") is None
		assert Camera.from_json(None) is None

	def test_equality_is_by_id(self):
		assert Camera("1", "A") == Camera("1", "B")
		assert len({Camera("1", "A"), Camera("1", "B")}) == 1


class TestStreamSet:
	def test_ignores_null_and_non_string_urls(self):
		streams = StreamSet({"high": RTSPS, "low": None, "medium": 42})
		assert streams.available() == ["high"]

	def test_available_is_ordered_best_first(self):
		streams = StreamSet({"low": "a", "high": "b", "medium": "c"})
		assert streams.available() == [QUALITY_HIGH, QUALITY_MEDIUM, QUALITY_LOW]

	def test_unknown_qualities_are_kept_after_the_known_ones(self):
		# Dual-sensor and doorbell cameras invent their own keys.
		streams = StreamSet({"high": "a", "sensor2": "b"})
		assert streams.available() == ["high", "sensor2"]

	def test_falls_back_when_the_wanted_quality_is_missing(self):
		# The user picks one quality globally, but a camera may not have that
		# tier enabled; without a fallback it would show nothing.
		streams = StreamSet({"low": "only-this"})
		assert streams.url_for(QUALITY_HIGH) == "only-this"

	def test_fallback_can_be_refused(self):
		streams = StreamSet({"low": "only-this"})
		assert streams.url_for(QUALITY_HIGH, fallback=False) is None

	def test_empty_set_yields_nothing(self):
		assert StreamSet({}).url_for(QUALITY_HIGH) is None


class TestPlainRtspConversion:
	def test_converts_scheme_port_and_strips_query(self):
		# The documented escape hatch for boxes whose ffmpeg cannot complete
		# TLS against the console's self-signed certificate.
		assert to_plain_rtsp(RTSPS) == "rtsp://10.0.0.1:7447/TOKEN123"

	def test_non_rtsps_url_returns_none(self):
		assert to_plain_rtsp("rtsp://host/x") is None
		assert to_plain_rtsp("http://host/x") is None
		assert to_plain_rtsp("") is None

	def test_non_standard_port_is_left_alone(self):
		# A console reached through a port forward: we cannot guess the
		# matching plain-RTSP forward, so keep what the user has.
		assert to_plain_rtsp("rtsps://host:9000/tok?enableSrtp") == (
			"rtsp://host:9000/tok")

	def test_hostname_without_port_gets_the_plain_port(self):
		assert to_plain_rtsp("rtsps://nvr.lan/tok") == "rtsp://nvr.lan:7447/tok"
