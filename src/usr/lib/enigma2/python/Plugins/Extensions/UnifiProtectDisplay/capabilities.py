# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""What this particular box can actually do.

The plugin runs on everything from single-decoder HD boxes to 4K quads, and
the difference decides which display modes may be offered at all. Probing once
at startup and disabling a mode with a stated reason beats letting the user
pick something that then fails with enigma2's own unhelpful popup.

Every probe is a filesystem check, injectable so the whole class can be tested
on a machine that is not a receiver.
"""

import os

# eVideoWidget drives the hardware scaler through /proc/stb/vmpeg/<decoder>/dst_*
# (lib/gui/evideo.cpp). Decoder 1 is the PiP decoder, so the presence of its
# node is the box's own statement that it has a second video decoder.
PIP_DECODER_PROBE = "/proc/stb/vmpeg/1/dst_left"

# serviceapp registers service types 5001/5002.
SERVICEAPP_DIR = "/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp"

# rtspsrc lives in gst-plugins-good; without it GStreamer cannot open an
# rtsp:// URI at all and fails with "no URI handler implemented for rtsp".
GST_RTSP_PLUGINS = (
	"/usr/lib/gstreamer-1.0/libgstrtsp.so",
	"/usr/lib64/gstreamer-1.0/libgstrtsp.so",
)

FFMPEG_NAMES = ("ffmpeg",)
BOXTYPE_FILES = ("/proc/stb/info/boxtype", "/proc/stb/info/model")

# Reasons the remux-PiP corner cannot run. Kept as codes so this module needs
# no gettext; the English text is for the log, and ui/ translates the codes.
BLOCKER_NO_PIP_DECODER = "no_pip_decoder"
BLOCKER_NO_FFMPEG = "no_ffmpeg"

BLOCKER_CODES = {
	BLOCKER_NO_PIP_DECODER: "this box has no second video decoder (no PiP)",
	BLOCKER_NO_FFMPEG: "ffmpeg is not installed (opkg install ffmpeg)",
}


class Capabilities(object):
	"""Snapshot of the box's abilities, taken once and cached.

	Cached deliberately: these are properties of the hardware and the
	installed packages, neither of which changes while enigma2 runs. A user
	who installs ffmpeg mid-session restarts enigma2 anyway.
	"""

	def __init__(self, exists=None, listdir=None, environ=None, read_text=None):
		self._exists = exists or os.path.exists
		self._listdir = listdir or os.listdir
		self._environ = environ if environ is not None else os.environ
		self._read_text = read_text or _read_text
		self._cache = {}

	# -- individual probes ------------------------------------------------

	def has_pip_decoder(self):
		"""True when the box exposes a second video decoder."""
		return self._cached("pip", lambda: self._exists(PIP_DECODER_PROBE))

	def has_ffmpeg(self):
		"""True when an ``ffmpeg`` binary is on PATH."""
		return self._cached("ffmpeg", lambda: self.which("ffmpeg") is not None)

	def has_serviceapp(self):
		"""True when serviceapp is installed, so 5001/5002 refs will play."""
		return self._cached("serviceapp", lambda: self._exists(SERVICEAPP_DIR))

	def has_gstreamer_rtsp(self):
		"""True when GStreamer can open rtsp:// URIs.

		A missing rtspsrc is the single most common reason a camera URL that
		works in VLC shows nothing on the box, so it is worth naming
		explicitly rather than letting playback fail silently.
		"""
		return self._cached(
			"gstrtsp",
			lambda: any(self._exists(path) for path in GST_RTSP_PLUGINS))

	def box_type(self):
		"""Box identifier for the log, or ``unknown``. Never used for logic."""
		def probe():
			for path in BOXTYPE_FILES:
				if self._exists(path):
					value = (self._read_text(path) or "").strip()
					if value:
						return value
			return "unknown"
		return self._cached("boxtype", probe)

	def which(self, name):
		"""Locate an executable on PATH. ``shutil.which`` is not on every image.

		Separators are hardcoded to POSIX rather than taken from ``os``: the
		PATH being parsed is always a receiver's, so using the host's
		conventions would be wrong when the tests run on Windows -- and
		right for the wrong reason everywhere else.
		"""
		path_value = self._environ.get("PATH") or "/usr/bin:/bin:/usr/sbin:/sbin"
		for directory in path_value.split(":"):
			if not directory:
				continue
			candidate = directory.rstrip("/") + "/" + name
			if self._exists(candidate):
				return candidate
		return None

	# -- derived answers --------------------------------------------------

	def can_remux_pip(self):
		"""True when the ffmpeg-remux PiP corner mode is usable.

		Both halves are required and neither can be worked around: without a
		second decoder there is nowhere to put the picture, and without
		ffmpeg there is nothing to turn RTSP into the MPEG-TS that the DVB
		service path demands.
		"""
		return self.has_pip_decoder() and self.has_ffmpeg()

	def remux_pip_blockers(self):
		"""Reasons :meth:`can_remux_pip` is false, as stable codes.

		Codes rather than sentences so this module stays free of gettext and
		the UI can render them in the user's language; see
		:data:`BLOCKER_CODES` for the full set.
		"""
		missing = []
		if not self.has_pip_decoder():
			missing.append(BLOCKER_NO_PIP_DECODER)
		if not self.has_ffmpeg():
			missing.append(BLOCKER_NO_FFMPEG)
		return missing

	def remux_pip_blocker(self):
		"""The same reasons as one English sentence, for logs.

		Returns ``None`` when the mode is available. The UI uses
		:meth:`remux_pip_blockers` instead so the text can be translated.
		"""
		codes = self.remux_pip_blockers()
		if not codes:
			return None
		return "; ".join(BLOCKER_CODES[code] for code in codes)

	def preferred_player(self):
		"""Default software player for fullscreen playback.

		exteplayer3 is preferred when present: it decodes RTSP through
		ffmpeg, which handles camera streams and RTSPS more reliably than
		GStreamer's rtspsrc on these images. GStreamer is the fallback
		because it is part of every image.
		"""
		return "exteplayer3" if self.has_serviceapp() else "gstreamer"

	def summary(self):
		"""One line for the startup log. Useful in every bug report."""
		return (
			"box=%s pip_decoder=%s ffmpeg=%s serviceapp=%s gst_rtsp=%s "
			"remux_pip=%s player=%s" % (
				self.box_type(), self.has_pip_decoder(), self.has_ffmpeg(),
				self.has_serviceapp(), self.has_gstreamer_rtsp(),
				self.can_remux_pip(), self.preferred_player()))

	# -- plumbing ---------------------------------------------------------

	def _cached(self, key, probe):
		if key not in self._cache:
			try:
				self._cache[key] = probe()
			except OSError:
				# A /proc read can fail on a locked-down image; treat an
				# unreadable probe as "feature absent" rather than crashing
				# the plugin at startup.
				self._cache[key] = False
		return self._cache[key]


def _read_text(path):
	try:
		with open(path, "r") as handle:
			return handle.read()
	except (OSError, IOError, UnicodeDecodeError):
		return ""
