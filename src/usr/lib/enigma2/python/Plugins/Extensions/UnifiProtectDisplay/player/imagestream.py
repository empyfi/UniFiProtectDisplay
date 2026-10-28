# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turning a series of pictures into a channel.

The offline card and the camera grid are pictures, but a bouquet entry has
to be a video service -- enigma2 has no notion of a channel that is a still
image. So the pictures are fed to ffmpeg as a JPEG sequence and come out as
the same MPEG-TS the real cameras arrive as. Nothing downstream needs a
special case.

Encoding is cheap here in a way it would not be for live video: the frames
are mostly flat colour and barely change, so ``ultrafast`` with
``stillimage`` tuning and a couple of frames a second costs a few percent of
one core. That is what makes a grid possible on a box that could never
re-encode four camera streams.
"""

import subprocess
import threading

# Two frames a second. Enough that a zap shows a picture quickly, slow
# enough that fetching a snapshot per camera per frame stays reasonable.
DEFAULT_FRAMERATE = 2

# Bounded so a slow consumer cannot make this grow without limit.
PIPE_CHUNK = 188 * 350


def ffmpeg_args(ffmpeg="ffmpeg", framerate=DEFAULT_FRAMERATE, bitrate="1200k"):
	"""Command line turning JPEGs on stdin into MPEG-TS on stdout.

	``-tune stillimage`` and a short GOP keep a zap from waiting on a
	keyframe: the decoder needs one before it can show anything, and with a
	long GOP that wait is exactly the "channel does not work" the grid is
	meant to avoid.
	"""
	return [
		ffmpeg,
		"-hide_banner",
		"-loglevel", "error",
		"-f", "image2pipe",
		"-framerate", str(framerate),
		"-i", "-",
		"-c:v", "libx264",
		"-preset", "ultrafast",
		"-tune", "stillimage",
		"-pix_fmt", "yuv420p",
		"-g", str(max(2, framerate)),
		"-b:v", bitrate,
		"-f", "mpegts",
		"-muxdelay", "0",
		"-",
	]


class ImageStreamer(object):
	"""Feeds frames to ffmpeg and pumps the result to a writer.

	``frame_source()`` returns JPEG bytes, or ``None`` to reuse the last
	frame -- which is what a grid does between snapshot fetches, so a slow
	console never stalls the stream.
	"""

	def __init__(self, frame_source, ffmpeg_path="ffmpeg",
			framerate=DEFAULT_FRAMERATE, log=None, popen=None,
			args_builder=None):
		self._frame_source = frame_source
		self._ffmpeg_path = ffmpeg_path
		self._framerate = framerate
		self._log = log
		self._popen = popen or subprocess.Popen
		self._args_builder = args_builder or ffmpeg_args

	def stream(self, write, should_continue=None):
		"""Run until the consumer goes away. Returns bytes written."""
		args = self._args_builder(self._ffmpeg_path, self._framerate)
		process = self._popen(
			args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
			stderr=subprocess.PIPE, close_fds=True)

		stop = threading.Event()
		feeder = threading.Thread(
			target=self._feed, args=(process, stop))
		feeder.daemon = True
		feeder.start()

		written = 0
		reader = getattr(process.stdout, "read1", None) or process.stdout.read
		try:
			while True:
				if should_continue is not None and not should_continue():
					break
				chunk = reader(PIPE_CHUNK)
				if not chunk:
					break
				write(chunk)
				written += len(chunk)
		except (IOError, OSError, ValueError):
			# Broken pipe: the viewer zapped away. The normal ending.
			pass
		finally:
			stop.set()
			self._terminate(process)
			feeder.join(timeout=3)
		return written

	def _feed(self, process, stop):
		"""Write one JPEG per frame interval until told to stop."""
		interval = 1.0 / float(max(1, self._framerate))
		last = None
		try:
			while not stop.is_set():
				try:
					frame = self._frame_source()
				except Exception as exc:  # noqa: BLE001
					self._warn("frame source failed: %s", exc)
					frame = None
				if frame:
					last = frame
				if last is None:
					# Nothing to send yet; wait rather than closing the
					# pipe, which ffmpeg would treat as end of input.
					stop.wait(interval)
					continue
				try:
					process.stdin.write(last)
					process.stdin.flush()
				except (IOError, OSError, ValueError):
					break
				stop.wait(interval)
		finally:
			try:
				process.stdin.close()
			except (IOError, OSError, ValueError):
				pass

	def _terminate(self, process):
		try:
			if process.poll() is None:
				process.terminate()
				try:
					process.wait(timeout=3)
				except Exception:  # noqa: BLE001
					process.kill()
			if process.stderr is not None:
				try:
					message = process.stderr.read() or b""
				except (IOError, OSError, ValueError):
					message = b""
				if message:
					self._warn("ffmpeg: %s",
						message.decode("utf-8", "replace").strip()[:300])
		except Exception as exc:  # noqa: BLE001
			self._warn("while stopping ffmpeg: %s", exc)
		finally:
			for stream in (process.stdout, process.stderr):
				try:
					if stream is not None:
						stream.close()
				except (IOError, OSError):
					pass

	def _warn(self, message, *args):
		if self._log is not None:
			self._log.error(message, *args)
