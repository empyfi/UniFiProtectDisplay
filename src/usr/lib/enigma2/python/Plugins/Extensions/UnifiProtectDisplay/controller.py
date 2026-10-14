# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Orchestration: what to ask the console for, and when.

Sits between the screens and :mod:`protect.api`. Every method here is
non-blocking -- the composite work (often two HTTP calls) runs as one unit on
a worker thread and the result is delivered on the main loop, so a screen
never waits and never has to chain callbacks itself.

Holds no enigma2 imports, so the whole decision layer is testable off the box.
"""

from .protect import errors
from .protect.models import to_plain_rtsp


class Settings(object):
	"""Plain-attribute view of the plugin's configuration.

	A deliberate seam: enigma2's ``config.*`` objects need the GUI to exist,
	so :mod:`settings` builds one of these from them and everything else --
	including every test -- works against this instead.
	"""

	def __init__(self, quality="high", use_plain_rtsp=False, player="auto",
			snapshot_high_quality=False):
		self.quality = quality
		self.use_plain_rtsp = use_plain_rtsp
		self.player = player
		self.snapshot_high_quality = snapshot_high_quality


class Controller(object):
	"""Camera data for the screens, fetched off the main loop."""

	def __init__(self, api, runner, store, capabilities=None, settings=None,
			log=None):
		self.api = api
		self.runner = runner
		self.store = store
		self.capabilities = capabilities
		self.settings = settings or Settings()
		self._log = log
		self._stream_cache = {}  # camera_id -> resolved URL

	# -- cameras ----------------------------------------------------------

	def refresh_cameras(self, on_done=None, on_error=None):
		"""Fetch the camera list and merge it into the store.

		``on_done`` receives ``(added, removed)``. The store is saved here
		rather than by the caller so a refresh cannot be left unpersisted.
		"""
		def work():
			cameras = self.api.list_cameras()
			added, removed = self.store.merge(cameras)
			self.store.save()
			return added, removed

		return self.runner.run(
			work, on_success=on_done, on_error=self._wrap_error(on_error),
			name="refresh_cameras")

	# -- streams ----------------------------------------------------------

	def stream_url(self, camera_id, on_done=None, on_error=None, enable=True):
		"""Resolve a playable RTSP URL for one camera.

		Does up to two calls in one go: read the camera's streams, and if
		none are enabled -- Protect's default -- turn on the configured
		quality. Doing the enable here is what spares the user from walking
		through the Protect UI per camera and copying tokens by hand.

		``enable`` is a safety valve: the setup screen passes False when it
		only wants to report the current state without writing to the
		console.
		"""
		cached = self._stream_cache.get(camera_id)
		if cached:
			# Deliver through the runner anyway, so callers see one
			# consistent (always asynchronous) contract.
			return self.runner.run(
				lambda: cached, on_success=on_done,
				on_error=self._wrap_error(on_error), name="stream_url_cached")

		quality = self.settings.quality

		def work():
			streams = self.api.get_rtsps_streams(camera_id)
			if not len(streams) and enable:
				streams = self.api.create_rtsps_streams(camera_id, quality)
			url = streams.url_for(quality)
			if not url:
				raise errors.ProtectError(
					"No RTSP stream available for this camera")
			if self.settings.use_plain_rtsp:
				plain = to_plain_rtsp(url)
				if plain:
					url = plain
			self._stream_cache[camera_id] = url
			return url

		return self.runner.run(
			work, on_success=on_done, on_error=self._wrap_error(on_error),
			name="stream_url")

	def invalidate_stream(self, camera_id=None):
		"""Drop cached URLs.

		Protect rotates a camera's stream token when RTSP is toggled, so a
		cached URL can go stale. Called on a playback failure and whenever
		the quality or transport setting changes.
		"""
		if camera_id is None:
			self._stream_cache.clear()
		else:
			self._stream_cache.pop(camera_id, None)

	def cached_stream_url(self, camera_id):
		return self._stream_cache.get(camera_id)

	# -- snapshots --------------------------------------------------------

	def snapshot(self, camera_id, on_done=None, on_error=None):
		"""Fetch one JPEG. ``on_done`` receives the raw bytes."""
		high = self.settings.snapshot_high_quality

		def work():
			return self.api.get_snapshot(camera_id, high_quality=high)

		return self.runner.run(
			work, on_success=on_done, on_error=self._wrap_error(on_error),
			name="snapshot")

	# -- connection check -------------------------------------------------

	def check_connection(self, on_done=None, on_error=None):
		"""Validate host and key. ``on_done`` receives the meta/info payload.

		The cheapest call Protect offers, which makes it the right one for a
		"test connection" button.
		"""
		return self.runner.run(
			self.api.meta_info, on_success=on_done,
			on_error=self._wrap_error(on_error), name="check_connection")

	# -- helpers ----------------------------------------------------------

	def player_name(self):
		"""Service player to use, resolving ``auto`` against the box."""
		configured = self.settings.player
		if configured and configured != "auto":
			return configured
		if self.capabilities is not None:
			return self.capabilities.preferred_player()
		return "gstreamer"

	def describe_error(self, exc):
		"""Turn an exception into one line fit for a TV screen.

		The user is holding a remote, not a debugger: say what to do, not
		what went wrong internally.
		"""
		if isinstance(exc, errors.ProtectAuthError):
			return "The API key was rejected. Create a new one in Protect " \
				"under Control Plane > Integrations."
		if isinstance(exc, errors.ProtectUnavailableError):
			return "The UniFi console did not answer. Check that it is " \
				"reachable and that the address is right."
		if isinstance(exc, errors.ProtectNotFoundError):
			return "This camera is no longer on the console. Refresh the " \
				"camera list."
		if isinstance(exc, errors.ProtectProtocolError):
			return "The console answered in a way this plugin did not " \
				"expect. It may run an unsupported Protect version."
		if isinstance(exc, errors.ProtectError):
			return str(exc)
		return "Unexpected error: %s" % (exc,)

	def _wrap_error(self, on_error):
		"""Log every failure, then hand it on."""
		def handler(exc):
			if self._log is not None:
				self._log.error("%s", self.describe_error(exc))
			if on_error is not None:
				on_error(exc)
		return handler
