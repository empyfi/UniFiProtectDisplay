# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Runtime capability probing.

The probes decide which display modes the user is even offered, so getting
them wrong means either hiding a mode that works or offering one that cannot.
"""

from UnifiProtectDisplay.capabilities import (
	Capabilities, PIP_DECODER_PROBE, SERVICEAPP_DIR, GST_RTSP_PLUGINS)

def caps(paths=(), path_env="/usr/bin:/bin", files=None):
	"""Capabilities over a fake filesystem containing exactly ``paths``."""
	present = set(paths)
	return Capabilities(
		exists=lambda p: p in present,
		environ={"PATH": path_env},
		read_text=lambda p: (files or {}).get(p, ""))

FFMPEG = "/usr/bin/ffmpeg"

class TestPipDecoder:
	def test_detected_from_the_proc_node(self):
		# eVideoWidget drives /proc/stb/vmpeg/<n>/dst_*; the node's presence
		# is the box's own statement that decoder 1 exists.
		assert caps([PIP_DECODER_PROBE]).has_pip_decoder() is True

	def test_absent_on_a_single_decoder_box(self):
		assert caps([]).has_pip_decoder() is False

class TestFfmpeg:
	def test_found_on_path(self):
		assert caps([FFMPEG]).has_ffmpeg() is True

	def test_absent_when_not_installed(self):
		assert caps([]).has_ffmpeg() is False

	def test_which_searches_every_path_entry(self):
		c = caps(["/opt/bin/ffmpeg"], path_env="/usr/bin:/opt/bin")
		assert c.which("ffmpeg") == "/opt/bin/ffmpeg"

	def test_which_returns_none_when_missing(self):
		assert caps([]).which("ffmpeg") is None

class TestServiceApp:
	def test_detected_from_the_plugin_directory(self):
		assert caps([SERVICEAPP_DIR]).has_serviceapp() is True

	def test_preferred_player_is_exteplayer3_when_available(self):
		# exteplayer3 decodes RTSP through ffmpeg, which handles camera
		# streams more reliably than GStreamer's rtspsrc on these images.
		assert caps([SERVICEAPP_DIR]).preferred_player() == "exteplayer3"

	def test_preferred_player_falls_back_to_gstreamer(self):
		# GStreamer is part of every image, so it is always a safe default.
		assert caps([]).preferred_player() == "gstreamer"

class TestGstreamerRtsp:
	def test_detected_from_the_plugin_object(self):
		assert caps([GST_RTSP_PLUGINS[0]]).has_gstreamer_rtsp() is True

	def test_any_known_library_path_counts(self):
		assert caps([GST_RTSP_PLUGINS[1]]).has_gstreamer_rtsp() is True

	def test_absent_without_gst_plugins_good_rtsp(self):
		# Without rtspsrc, a URL that plays in VLC shows nothing on the box.
		assert caps([]).has_gstreamer_rtsp() is False

class TestRemuxPipGate:
	def test_available_with_both_decoder_and_ffmpeg(self):
		c = caps([PIP_DECODER_PROBE, FFMPEG])
		assert c.can_remux_pip() is True
		assert c.remux_pip_blocker() is None

	def test_blocked_without_a_second_decoder(self):
		c = caps([FFMPEG])
		assert c.can_remux_pip() is False
		assert "second video decoder" in c.remux_pip_blocker()

	def test_blocked_without_ffmpeg(self):
		c = caps([PIP_DECODER_PROBE])
		assert c.can_remux_pip() is False
		assert "ffmpeg" in c.remux_pip_blocker()

	def test_blocker_names_both_causes(self):
		# The user should fix everything in one pass, not discover the
		# second problem after solving the first.
		blocker = caps([]).remux_pip_blocker()
		assert "decoder" in blocker and "ffmpeg" in blocker

class TestBoxType:
	def test_read_from_proc(self):
		c = Capabilities(
			exists=lambda p: p == "/proc/stb/info/boxtype",
			environ={"PATH": ""},
			read_text=lambda p: "gbquad4kpro\n")
		assert c.box_type() == "gbquad4kpro"

	def test_unknown_when_absent(self):
		assert caps([]).box_type() == "unknown"

class TestRobustness:
	def test_an_unreadable_probe_counts_as_absent(self):
		# A locked-down image must not crash the plugin at startup.
		def boom(path):
			raise OSError("permission denied")

		c = Capabilities(exists=boom, environ={"PATH": "/usr/bin"})
		assert c.has_pip_decoder() is False
		assert c.can_remux_pip() is False

	def test_probes_are_cached(self):
		calls = []

		def counting_exists(path):
			calls.append(path)
			return False

		c = Capabilities(exists=counting_exists, environ={"PATH": ""})
		c.has_pip_decoder()
		c.has_pip_decoder()
		assert calls.count(PIP_DECODER_PROBE) == 1

	def test_summary_mentions_every_probe(self):
		summary = caps([PIP_DECODER_PROBE, FFMPEG]).summary()
		for key in ("box=", "pip_decoder=", "ffmpeg=", "serviceapp=",
				"gst_rtsp=", "remux_pip=", "player="):
			assert key in summary
