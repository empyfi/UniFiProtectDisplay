# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turning still pictures into a channel, and how fast it starts.

The offline card and the grid are both encoded here, and both are things
somebody zaps to. A card that takes six seconds to appear is read as a
channel that does not work, which defeats the point of having a card at
all -- so the startup path is pinned down rather than left to chance.
"""

import threading

from UnifiProtectDisplay.player import imagestream
from UnifiProtectDisplay.player.imagestream import ImageStreamer

JPEG = b"\xff\xd8not-really-a-jpeg"


class RecordingStdin:
	"""Collects what the feeder writes, instead of a pipe to ffmpeg."""

	def __init__(self):
		self.writes = []
		self.closed = False

	def write(self, data):
		self.writes.append(data)

	def flush(self):
		pass

	def close(self):
		self.closed = True


class FakeProcess:
	def __init__(self):
		self.stdin = RecordingStdin()


class TestFfmpegCommand:
	def test_input_probing_is_cut_short(self):
		# The default is five seconds of stream time spent working out what
		# the input is. There is nothing to work out: every frame is a JPEG
		# this plugin just rendered.
		args = imagestream.ffmpeg_args()
		assert "-analyzeduration" in args
		assert "-probesize" in args
		# Probing options only take effect ahead of the input they describe.
		assert args.index("-analyzeduration") < args.index("-i")
		assert args.index("-probesize") < args.index("-i")

	def test_output_is_mpegts_on_stdout(self):
		args = imagestream.ffmpeg_args()
		assert args[-3:] == ["-muxdelay", "0", "-"]
		assert "mpegts" in args

	def test_the_gop_is_short_enough_to_zap_into(self):
		# A decoder shows nothing until a keyframe arrives.
		args = imagestream.ffmpeg_args(framerate=2)
		assert int(args[args.index("-g") + 1]) <= 2


class TestPriming:
	"""Why the first picture goes in more than once."""

	@staticmethod
	def feed(source, framerate=1000):
		"""Run the feeder until the source asks it to stop."""
		stop = threading.Event()
		process = FakeProcess()
		streamer = ImageStreamer(
			lambda: source(stop), framerate=framerate)
		streamer._feed(process, stop)
		return process.stdin

	def test_the_first_frame_goes_in_several_times(self):
		calls = []

		def source(stop):
			calls.append(1)
			if len(calls) >= 2:
				stop.set()
			return JPEG

		stdin = self.feed(source)
		assert len(stdin.writes) >= imagestream.PRIME_FRAMES
		assert stdin.writes[:imagestream.PRIME_FRAMES] == (
			[JPEG] * imagestream.PRIME_FRAMES)

	def test_priming_does_not_refetch_the_picture(self):
		# For the grid the source is a snapshot request per camera. Priming
		# must copy the frame already in hand, not ask for four more.
		calls = []

		def source(stop):
			calls.append(1)
			if len(calls) >= 2:
				stop.set()
			return JPEG

		stdin = self.feed(source)
		assert len(calls) == 2
		assert len(stdin.writes) > len(calls)

	def test_later_frames_go_in_one_at_a_time(self):
		calls = []

		def source(stop):
			calls.append(1)
			if len(calls) >= 3:
				stop.set()
			return JPEG

		stdin = self.feed(source)
		assert len(stdin.writes) == imagestream.PRIME_FRAMES + 2

	def test_nothing_is_written_before_there_is_a_picture(self):
		# Closing the pipe would be end-of-input to ffmpeg, so an empty
		# source has to wait rather than write or close.
		calls = []

		def source(stop):
			calls.append(1)
			if len(calls) >= 2:
				stop.set()
			return None

		stdin = self.feed(source)
		assert stdin.writes == []

	def test_a_failing_source_does_not_take_the_stream_down(self):
		calls = []

		def source(stop):
			calls.append(1)
			if len(calls) >= 3:
				stop.set()
			if len(calls) == 2:
				raise RuntimeError("console went away")
			return JPEG

		stdin = self.feed(source)
		# The last good picture is shown again rather than the channel
		# ending: a console that blinks must not blank the television.
		assert len(stdin.writes) >= imagestream.PRIME_FRAMES + 1
		assert all(write == JPEG for write in stdin.writes)

	def test_the_pipe_is_closed_when_the_feed_ends(self):
		def source(stop):
			stop.set()
			return JPEG

		stdin = self.feed(source)
		assert stdin.closed
