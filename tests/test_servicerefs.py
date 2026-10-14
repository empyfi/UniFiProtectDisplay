# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Service reference escaping.

These assertions mirror enigma2's own ``encode()`` and ``urlDecode()`` in
``lib/service/service.cpp`` and ``lib/base/estring.cpp``. They are the reason
the plugin can be confident a camera URL survives into enigma2 intact -- a
mistake here produces a reference that parses fine and plays nothing.
"""

import pytest

from UnifiProtectDisplay import servicerefs as sr

RTSPS_URL = "rtsps://192.168.1.1:7441/AbC123?enableSrtp"


def parse_like_enigma2(reference):
	"""Split a reference the way ``eServiceReference(const std::string&)`` does.

	Ten numeric fields, then the path up to the first literal colon, then the
	name; both are passed through urlDecode.
	"""
	rest = reference.split(":", 10)[10]
	path, _, name = rest.partition(":")
	return sr.decode_component(path), sr.decode_component(name)


class TestEncoding:
	def test_escapes_colon_as_lowercase_hex(self):
		assert sr.encode_component("a:b") == "a%3ab"

	def test_escapes_percent_so_existing_escapes_survive(self):
		# Without this, a URL containing %20 would decode to a space and the
		# request would go to a different path than intended.
		assert sr.encode_component("a%20b") == "a%2520b"
		assert sr.decode_component(sr.encode_component("a%20b")) == "a%20b"

	def test_escapes_control_characters(self):
		assert sr.encode_component("a\nb") == "a%0ab"
		assert sr.encode_component("a\x01b") == "a%01b"

	@pytest.mark.parametrize("char", ["?", "&", "=", "/", "-", "_", ".", "~", "+"])
	def test_leaves_url_punctuation_alone(self, char):
		# enigma2's encode() touches only ':', '%' and control characters.
		# Escaping more would corrupt Protect's ?enableSrtp query.
		assert sr.encode_component("a%sb" % char) == "a%sb" % char

	def test_empty_input(self):
		assert sr.encode_component("") == ""
		assert sr.encode_component(None) == ""


class TestDecoding:
	def test_round_trip_preserves_url(self):
		assert sr.decode_component(sr.encode_component(RTSPS_URL)) == RTSPS_URL

	def test_truncated_escape_at_end_is_dropped(self):
		# enigma2 advances past the escape and breaks out of the loop.
		assert sr.decode_component("ab%") == "ab"
		assert sr.decode_component("ab%3") == "ab"

	def test_null_byte_escape_is_dropped(self):
		# urlDecode only appends when strtoul yields non-zero.
		assert sr.decode_component("a%00b") == "ab"

	def test_invalid_hex_is_skipped(self):
		assert sr.decode_component("a%zzb") == "ab"


class TestBuildReference:
	def test_field_layout(self):
		ref = sr.build_reference(RTSPS_URL, "Cam")
		fields = ref.split(":")
		assert fields[0] == "4097"
		assert fields[1] == "0"
		assert fields[2] == "1"  # data[0]: digital television
		assert fields[3:10] == ["0"] * 7

	def test_url_and_name_round_trip(self):
		ref = sr.build_reference(RTSPS_URL, "Hof")
		path, name = parse_like_enigma2(ref)
		assert path == RTSPS_URL
		assert name == "Hof"

	def test_colon_in_name_does_not_split_the_reference(self):
		ref = sr.build_reference(RTSPS_URL, "Hof: Tor")
		path, name = parse_like_enigma2(ref)
		assert path == RTSPS_URL
		assert name == "Hof: Tor"

	def test_name_is_optional(self):
		ref = sr.build_reference(RTSPS_URL)
		assert ref.endswith(sr.encode_component(RTSPS_URL))

	def test_empty_url_is_rejected(self):
		# Silently building a reference to nothing would show as a black
		# screen with no explanation.
		with pytest.raises(ValueError):
			sr.build_reference("")


class TestPlayerSelection:
	@pytest.mark.parametrize("player,expected", [
		("gstreamer", "4097"),
		("exteplayer3", "5002"),
		("gstplayer", "5001"),
	])
	def test_player_maps_to_service_type(self, player, expected):
		ref = sr.build_stream_reference(RTSPS_URL, "Cam", player)
		assert ref.split(":")[0] == expected

	def test_unknown_player_falls_back_to_gstreamer(self):
		# GStreamer is the only backend present on every image, so an
		# unexpected config value must not leave the user with nothing.
		ref = sr.build_stream_reference(RTSPS_URL, "Cam", "nonsense")
		assert ref.split(":")[0] == "4097"


class TestPipReference:
	def test_uses_dvb_service_type(self):
		# Type 1 is not a style choice: every software player returns -1 from
		# setTarget(), so only the DVB path can occupy the PiP decoder.
		ref = sr.build_pip_reference("http://127.0.0.1:8099/cam/tok", "Cam")
		assert ref.split(":")[0] == "1"

	def test_local_url_round_trips(self):
		url = "http://127.0.0.1:8099/cam/tok"
		path, _ = parse_like_enigma2(sr.build_pip_reference(url, "Cam"))
		assert path == url


class TestIsStreamReference:
	def test_detects_url_reference(self):
		assert sr.is_stream_reference(sr.build_reference(RTSPS_URL, "Cam"))

	def test_rejects_tuner_reference(self):
		assert not sr.is_stream_reference("1:0:19:283D:3FB:1:C00000:0:0:0:")

	def test_rejects_garbage(self):
		assert not sr.is_stream_reference("")
		assert not sr.is_stream_reference("1:0:1")
