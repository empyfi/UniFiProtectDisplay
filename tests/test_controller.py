# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Orchestration between the screens and the Protect API."""

import pytest

from UnifiProtectDisplay.camerastore import CameraStore
from UnifiProtectDisplay.controller import Controller, Settings
from UnifiProtectDisplay.protect import errors
from UnifiProtectDisplay.protect.models import Camera, StreamSet

RTSPS = "rtsps://10.0.0.1:7441/TOK?enableSrtp"


class SyncRunner:
	"""Runs work immediately, so tests read top to bottom.

	The asynchrony itself is covered in test_async_util; here the question
	is what gets asked of the console and what comes back.
	"""

	def __init__(self):
		self.names = []

	def run(self, func, on_success=None, on_error=None, name=""):
		self.names.append(name)
		try:
			result = func()
		except Exception as exc:  # noqa: BLE001
			if on_error:
				on_error(exc)
			return None
		if on_success:
			on_success(result)
		return None


class FakeApi:
	def __init__(self, cameras=None, streams=None, snapshot=b"jpeg",
			meta=None, raises=None):
		self._cameras = cameras or []
		self._streams = streams if streams is not None else StreamSet({})
		self._snapshot = snapshot
		self._meta = meta or {"applicationVersion": "6.0.0"}
		self._raises = raises or {}
		self.created = []
		self.snapshot_calls = []

	def _maybe_raise(self, key):
		if key in self._raises:
			raise self._raises[key]

	def list_cameras(self):
		self._maybe_raise("list_cameras")
		return self._cameras

	def get_rtsps_streams(self, camera_id):
		self._maybe_raise("get_rtsps_streams")
		return self._streams

	def create_rtsps_streams(self, camera_id, qualities):
		self._maybe_raise("create_rtsps_streams")
		self.created.append((camera_id, qualities))
		self._streams = StreamSet({"high": RTSPS})
		return self._streams

	def get_snapshot(self, camera_id, high_quality=False):
		self._maybe_raise("get_snapshot")
		self.snapshot_calls.append((camera_id, high_quality))
		return self._snapshot

	def meta_info(self):
		self._maybe_raise("meta_info")
		return self._meta


def make(api=None, settings=None, capabilities=None):
	backing = {"text": ""}
	store = CameraStore(
		reader=lambda p: backing["text"],
		writer=lambda p, t: backing.update(text=t))
	store.load()
	controller = Controller(
		api or FakeApi(), SyncRunner(), store,
		capabilities=capabilities, settings=settings or Settings())
	return controller


class TestRefreshCameras:
	def test_merges_and_persists(self):
		controller = make(FakeApi(cameras=[Camera("1", "Hof")]))
		result = []
		controller.refresh_cameras(on_done=result.append)
		assert result == [(1, 0)]
		assert controller.store.name_for("1") == "Hof"

	def test_failure_reaches_the_error_handler(self):
		api = FakeApi(raises={"list_cameras": errors.ProtectAuthError("nope")})
		controller = make(api)
		failures = []
		controller.refresh_cameras(on_error=failures.append)
		assert isinstance(failures[0], errors.ProtectAuthError)


class TestStreamUrl:
	def test_returns_an_already_enabled_stream(self):
		controller = make(FakeApi(streams=StreamSet({"high": RTSPS})))
		got = []
		controller.stream_url("cam1", on_done=got.append)
		assert got == [RTSPS]

	def test_enables_rtsp_when_the_camera_has_none(self):
		# Protect ships with RTSP off per camera; enabling it here is what
		# spares the user the Protect UI and the token copying.
		api = FakeApi(streams=StreamSet({}))
		controller = make(api)
		got = []
		controller.stream_url("cam1", on_done=got.append)
		assert api.created == [("cam1", "high")]
		assert got == [RTSPS]

	def test_enable_can_be_refused(self):
		# The setup screen reports state without writing to the console.
		api = FakeApi(streams=StreamSet({}))
		controller = make(api)
		failures = []
		controller.stream_url("cam1", on_error=failures.append, enable=False)
		assert api.created == []
		assert isinstance(failures[0], errors.ProtectError)

	def test_configured_quality_is_requested(self):
		api = FakeApi(streams=StreamSet({}))
		controller = make(api, settings=Settings(quality="low"))
		controller.stream_url("cam1")
		assert api.created == [("cam1", "low")]

	def test_falls_back_to_another_quality(self):
		# A camera may not have the globally configured tier enabled.
		controller = make(
			FakeApi(streams=StreamSet({"low": RTSPS})),
			settings=Settings(quality="high"))
		got = []
		controller.stream_url("cam1", on_done=got.append)
		assert got == [RTSPS]

	def test_plain_rtsp_conversion_when_configured(self):
		# The escape hatch for boxes whose ffmpeg cannot do TLS against the
		# console's self-signed certificate.
		controller = make(
			FakeApi(streams=StreamSet({"high": RTSPS})),
			settings=Settings(use_plain_rtsp=True))
		got = []
		controller.stream_url("cam1", on_done=got.append)
		assert got == ["rtsp://10.0.0.1:7447/TOK"]

	def test_result_is_cached(self):
		api = FakeApi(streams=StreamSet({"high": RTSPS}))
		controller = make(api)
		controller.stream_url("cam1")
		controller.stream_url("cam1")
		assert controller.runner.names[-1] == "stream_url_cached"

	def test_cached_value_is_still_delivered_asynchronously(self):
		# One contract for callers: the answer always arrives by callback.
		controller = make(FakeApi(streams=StreamSet({"high": RTSPS})))
		controller.stream_url("cam1")
		got = []
		controller.stream_url("cam1", on_done=got.append)
		assert got == [RTSPS]

	def test_invalidate_forces_a_refetch(self):
		# Protect rotates the token when RTSP is toggled, so a cached URL
		# can go stale.
		controller = make(FakeApi(streams=StreamSet({"high": RTSPS})))
		controller.stream_url("cam1")
		controller.invalidate_stream("cam1")
		assert controller.cached_stream_url("cam1") is None
		controller.stream_url("cam1")
		assert controller.runner.names[-1] == "stream_url"

	def test_invalidate_all(self):
		controller = make(FakeApi(streams=StreamSet({"high": RTSPS})))
		controller.stream_url("cam1")
		controller.invalidate_stream()
		assert controller.cached_stream_url("cam1") is None


class TestSnapshot:
	def test_returns_bytes(self):
		controller = make(FakeApi(snapshot=b"\xff\xd8data"))
		got = []
		controller.snapshot("cam1", on_done=got.append)
		assert got == [b"\xff\xd8data"]

	def test_low_quality_by_default(self):
		# The picture is scaled into a corner; a large JPEG costs console
		# time and decode time for nothing.
		api = FakeApi()
		controller = make(api)
		controller.snapshot("cam1")
		assert api.snapshot_calls == [("cam1", False)]

	def test_high_quality_when_configured(self):
		api = FakeApi()
		controller = make(api, settings=Settings(snapshot_high_quality=True))
		controller.snapshot("cam1")
		assert api.snapshot_calls == [("cam1", True)]


class TestPlayerSelection:
	class Caps:
		def __init__(self, preferred):
			self._preferred = preferred

		def preferred_player(self):
			return self._preferred

	def test_explicit_choice_wins(self):
		controller = make(
			settings=Settings(player="gstreamer"),
			capabilities=self.Caps("exteplayer3"))
		assert controller.player_name() == "gstreamer"

	def test_auto_asks_the_box(self):
		controller = make(
			settings=Settings(player="auto"),
			capabilities=self.Caps("exteplayer3"))
		assert controller.player_name() == "exteplayer3"

	def test_auto_without_capabilities_falls_back_to_gstreamer(self):
		controller = make(settings=Settings(player="auto"))
		assert controller.player_name() == "gstreamer"


class TestErrorMessages:
	@pytest.mark.parametrize("exc,needle", [
		(errors.ProtectAuthError("x"), "Integrations"),
		(errors.ProtectUnavailableError("x"), "did not answer"),
		(errors.ProtectNotFoundError("x"), "Refresh"),
		(errors.ProtectProtocolError("x"), "Protect version"),
	])
	def test_messages_say_what_to_do(self, exc, needle):
		# The user is holding a remote, not a debugger.
		assert needle in make().describe_error(exc)

	def test_unknown_exception_still_produces_a_message(self):
		assert "Unexpected" in make().describe_error(RuntimeError("boom"))

	def test_errors_are_logged_once(self):
		logged = []

		class Log:
			def error(self, message, *args):
				logged.append(message % args if args else message)

		controller = make(
			FakeApi(raises={"meta_info": errors.ProtectAuthError("x")}))
		controller._log = Log()
		controller.check_connection(on_error=lambda e: None)
		assert len(logged) == 1


class TestConnectionCheck:
	def test_returns_meta_info(self):
		controller = make(FakeApi(meta={"applicationVersion": "6.1.0"}))
		got = []
		controller.check_connection(on_done=got.append)
		assert got[0]["applicationVersion"] == "6.1.0"
