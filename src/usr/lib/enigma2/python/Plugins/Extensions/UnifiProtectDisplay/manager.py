# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one object that owns the plugin's state for a session.

Everything with a lifetime longer than a single screen lives here: the API
client, the worker pool, the camera store, the rotation cursor, and whichever
corner mode is currently running. The setup screen and the extensions menu
both talk to this, so a settings change takes effect in one place rather than
being re-derived in three.

A single instance per enigma2 session, created at session start.
"""

import time

from enigma import eTimer

from . import settings as settings_module
from .async_util import TaskRunner
from .camerastore import CameraStore
from .capabilities import Capabilities
from .controller import Controller
from .logger import Logger
from .protect.api import ProtectApi
from .rotation import Rotation
from .settings import MODE_OFF, MODE_PIP, MODE_SNAPSHOT
from .localisation import _


# How often the camera list may be re-fetched while switching around.
# Without a floor, holding the next-camera button would fire one request per
# press; with it, a camera that has just come back is picked up within a few
# seconds, which is as precise as "is it online" can usefully be.
REFRESH_INTERVAL = 5.0

# How old the stored connected flags may be before a channel in the
# bouquet re-reads them before playing. Long enough that paging through
# the cameras costs one request, short enough that a camera unplugged
# this morning is not still "connected" tonight.
CONNECTED_TTL = 30.0

# How often the rotation channel checks whether the camera on screen
# has had its turn. A second is far finer than any interval worth
# setting, and the check is a comparison of two numbers.
ZAP_TICK_MS = 1000

# And how long to wait before actually changing channel. The decision
# is taken inside enigma2's own service-started callback, and calling
# playService() from in there is re-entering the thing that called us.
# A one-shot timer puts the zap back on the main loop, where an
# ordinary channel change happens.
ZAP_DELAY_MS = 50

# How often the grid refetches snapshots. One request per camera per frame
# would hammer the console for a picture nobody is studying that closely.
GRID_INTERVAL = 2.0


class DisplayManager(object):
	"""Owns the plugin's long-lived state for one enigma2 session."""

	instance = None

	def __init__(self, session):
		self.session = session
		config = settings_module.get_config()
		self.log = Logger(debug=config.debug.value)
		self.capabilities = Capabilities()
		self.log.info("starting: %s", self.capabilities.summary())

		self.runner = TaskRunner(timer_factory=eTimer, log=self.log)
		self.store = CameraStore(log=self.log).load()
		self.rotation = Rotation(
			self.store.enabled_ids(), enabled=config.rotate.value,
			is_usable=self._camera_usable)
		self.api = self._build_api(config)
		self.controller = Controller(
			self.api, self.runner, self.store,
			capabilities=self.capabilities,
			settings=settings_module.build_settings(config),
			log=self.log)

		self._overlay = None
		self._pip = None
		self._last_refresh = 0.0
		self._refresh_task = None
		self._webhook = None
		self._motion_until = 0.0
		self._bouquet_proxy = None
		self._grid_cache = None
		self._grid_fetched = 0.0
		self._connected_checked = 0.0
		self._zap_rotation = None
		self._zap_hooked = False
		self._zap_timer = None
		self._zap_once = None
		self._zap_target = None

	@classmethod
	def get(cls, session=None):
		"""The session's manager, created on first use."""
		if cls.instance is None and session is not None:
			cls.instance = cls(session)
		return cls.instance

	# -- configuration ----------------------------------------------------

	def note_camera_shown(self, force=False):
		"""Re-check which cameras are reachable, in the background.

		Called whenever a camera is put on screen or switched, because the
		offline flag only matters at that moment and is otherwise as old as
		the last camera-list fetch. It never blocks the picture: the result
		arrives later and only changes which cameras stepping will skip.
		"""
		now = time.time()
		if not force and now - self._last_refresh < REFRESH_INTERVAL:
			return
		if self._refresh_task is not None:
			return  # one in flight is enough
		if not self.api.is_configured():
			return
		self._last_refresh = now
		self._refresh_task = self.controller.refresh_cameras(
			on_done=self._state_refreshed, on_error=self._refresh_failed)

	def _state_refreshed(self, counts):
		self._refresh_task = None
		# Keep the cursor on its camera; only the usable set may have moved.
		self.rotation.set_cameras(self.store.enabled_ids())
		added, removed = counts
		if added or removed:
			self.log.info("camera list changed: %d new, %d gone", added, removed)

	def _refresh_failed(self, exc):
		self._refresh_task = None
		# A failed refresh is not worth disturbing playback over; the
		# previous state simply stays in force.
		self.log.debug("camera list refresh failed: %s", exc)

	def _camera_usable(self, camera_id):
		"""Skip cameras the console last reported as offline.

		They decode to nothing, so stepping onto one is indistinguishable
		from the button not having worked.
		"""
		if not settings_module.get_config().skip_offline.value:
			return True
		return self.store.is_connected(camera_id)

	def _build_api(self, config):
		return ProtectApi(
			host=config.host.value,
			api_key=config.apikey.value,
			verify_ssl=config.verify_ssl.value,
			log=self.log)

	def reload_settings(self):
		"""Re-read the config after the setup screen closes.

		Rebuilds the API client and the settings snapshot, and restarts the
		corner if it was running, so geometry and interval changes apply
		immediately rather than at the next reboot.
		"""
		config = settings_module.get_config()
		self.log.debug_enabled = config.debug.value
		self.api = self._build_api(config)
		self.controller.api = self.api
		self.controller.settings = settings_module.build_settings(config)
		# A changed quality or transport makes every cached URL wrong.
		self.controller.invalidate_stream()
		self.rotation.enabled = config.rotate.value
		self.rotation.set_cameras(self.store.enabled_ids())
		self.apply_key_binding()
		self.stop_webhook()
		self.start_webhook()
		self.stop_bouquet_proxy()
		self.start_bouquet_proxy()
		self.start_zap_rotation()
		self.sync_bouquet()

		# Restart only what was already on screen. Saving settings is not a
		# request to switch the corner on, and a corner appearing by itself
		# because the user adjusted an unrelated option would be surprising.
		was_running = self.corner_running()
		self.stop_corner()
		if was_running and config.corner_mode.value != MODE_OFF:
			self.start_corner()

	def refresh_selection(self):
		"""Pick up a change made in the camera list screen."""
		self.rotation.set_cameras(self.store.enabled_ids())
		# The rotation channel holds one reference per camera, so a
		# changed selection makes its list wrong.
		self.start_zap_rotation()

	# -- the corner -------------------------------------------------------

	def pip_corner(self):
		"""The running live-video corner, or None."""
		return self._pip if (self._pip is not None and self._pip.active) else None

	def corner_running(self):
		return self._overlay is not None or (
			self._pip is not None and self._pip.active)

	def start_corner(self):
		"""Start whichever corner mode is configured. Returns ``(ok, message)``."""
		config = settings_module.get_config()
		mode = config.corner_mode.value
		if mode == MODE_OFF:
			return False, _("The corner display is switched off in setup.")
		if self.corner_running():
			return True, ""
		if not self.rotation.camera_ids():
			return False, _(
				"No cameras are selected. Open the camera list first.")
		self.note_camera_shown(force=True)
		if mode == MODE_SNAPSHOT:
			return self._start_snapshot(config)
		if mode == MODE_PIP:
			return self._start_pip(config)
		return False, _("Unknown display mode: %s") % mode

	def _start_snapshot(self, config):
		from .render.snapshot_overlay import SnapshotOverlay
		try:
			self._overlay = self.session.instantiateDialog(
				SnapshotOverlay,
				self.controller, self.rotation,
				corner=config.corner.value,
				width_percent=config.corner_width.value,
				margin_percent=config.corner_margin.value,
				interval=config.snapshot_interval.value,
				rotate_interval=config.rotate_interval.value,
				follow_pip=config.follow_pip.value,
				on_camera_shown=self.note_camera_shown,
				log=self.log)
			self._overlay.show()
		except Exception as exc:  # noqa: BLE001
			self.log.error("could not start the snapshot overlay: %s", exc)
			self._overlay = None
			return False, _("Could not start the overlay: %s") % exc
		return True, ""

	def _start_pip(self, config):
		from .player.pip_remux import PipCorner
		self._pip = PipCorner(
			self.session, self.controller, self.rotation, self.capabilities,
			corner=config.corner.value,
			width_percent=config.corner_width.value,
			margin_percent=config.corner_margin.value,
			rotate_interval=config.rotate_interval.value,
			proxy_port=config.proxy_port.value,
			follow_pip=config.follow_pip.value,
			fit=config.corner_fit.value,
			on_camera_shown=self.note_camera_shown,
			log=self.log)
		ok, message = self._pip.start()
		if not ok:
			self._pip = None
		return ok, message

	def stop_corner(self):
		if self._overlay is not None:
			try:
				# deleteDialog hides and closes, which runs the overlay's
				# own onClose cleanup (timers, pending fetch, spool files).
				self.session.deleteDialog(self._overlay)
			except Exception as exc:  # noqa: BLE001
				self.log.error("while closing the overlay: %s", exc)
			self._overlay = None
		if self._pip is not None:
			self._pip.stop()
			self._pip = None

	def toggle_corner(self):
		if self.corner_running():
			self.stop_corner()
			return True, ""
		return self.start_corner()

	# -- fullscreen -------------------------------------------------------

	def open_fullscreen(self):
		"""Open the fullscreen viewer.

		The corner is stopped first: in snapshot mode it would sit on top of
		the fullscreen picture, and in PiP mode both would compete for the
		camera and the decoders.
		"""
		if not self.rotation.camera_ids():
			return False, _(
				"No cameras are selected. Open the camera list first.")
		self.note_camera_shown(force=True)
		self.stop_corner()
		from .player.fullscreen import FullscreenViewer
		config = settings_module.get_config()
		self.session.open(
			FullscreenViewer, self.controller, self.rotation,
			rotate_interval=config.rotate_interval.value, log=self.log,
			capabilities=self.capabilities,
			proxy_port=config.proxy_port.value,
			on_camera_shown=self.note_camera_shown)
		return True, ""

	# -- misc -------------------------------------------------------------

	# -- the channel-list bouquet -----------------------------------------

	def start_bouquet_proxy(self):
		"""Keep a proxy on a fixed port, so bouquet entries stay playable.

		Different from the one the corner uses: that one takes any free
		port and invents a token per session, which is fine for a reference
		built moments earlier and useless for one sitting in a channel
		list. Camera URLs are resolved on first play rather than at start,
		so booting costs no Protect requests.
		"""
		config = settings_module.get_config()
		if not config.bouquet.value or self._bouquet_proxy is not None:
			return
		from .player.tsproxy import TsProxy
		ffmpeg = self.capabilities.which("ffmpeg") or "ffmpeg"
		proxy = TsProxy(
			ffmpeg_path=ffmpeg, port=config.bouquet_port.value,
			log=self.log, url_resolver=self._resolve_token,
			offline_card=self._offline_card, grid_source=self._grid_frame,
			problem_card=self._problem_card,
			rotate_source=self._rotate_frame)
		try:
			proxy.start()
		except Exception as exc:  # noqa: BLE001
			self.log.error("could not start the bouquet proxy on port %s: %s",
				config.bouquet_port.value, exc)
			return
		for camera_id in self.store.enabled_ids():
			token = self.store.token_for(camera_id)
			if token:
				proxy.register_token(camera_id, token)
		self.store.save()
		self._bouquet_proxy = proxy

	def stop_bouquet_proxy(self):
		if self._bouquet_proxy is not None:
			self._bouquet_proxy.stop()
			self._bouquet_proxy = None
		self._grid_cache = None
		self._grid_fetched = 0.0
		self._connected_checked = 0.0

	def _resolve_token(self, token):
		"""Look up a camera's stream when its bouquet entry is first played.

		Runs on a proxy thread, so blocking on the Protect API here is fine
		and keeps the main loop out of it entirely.
		"""
		camera_id = self.store.camera_for_token(token)
		if camera_id is None:
			return None
		cached = self.controller.cached_stream_url(camera_id)
		if cached:
			return camera_id, cached
		try:
			streams = self.api.get_rtsps_streams(camera_id)
			if not len(streams):
				streams = self.api.create_rtsps_streams(
					camera_id, self.controller.settings.quality)
			url = streams.url_for(self.controller.settings.quality)
		except Exception as exc:  # noqa: BLE001
			self.log.error("could not resolve the stream for %s: %s",
				camera_id, exc)
			return None
		if not url:
			return None
		if self.controller.settings.use_plain_rtsp:
			from .protect.models import to_plain_rtsp
			url = to_plain_rtsp(url) or url
		return camera_id, url

	def _offline_card(self, camera_id):
		"""A card for a camera the console cannot reach, else None.

		Returning None means "stream it normally", so this is also the one
		place deciding what offline means for the channel list.
		"""
		from . import cards
		if not cards.available():
			return None
		if self._connected_now(camera_id):
			return None
		return cards.offline_card(self.store.name_for(camera_id))

	def _connected_now(self, camera_id):
		"""Whether the console reaches this camera, from a recent answer.

		The stored flag is written when the camera list is fetched, and on
		a box nobody has opened the plugin on that may have been days ago.
		A camera unplugged since then still reads as connected, so the
		channel goes off to remux a stream that no longer exists, sits
		black for eleven seconds and then fails -- measured on a receiver,
		and from the sofa indistinguishable from a broken plugin.

		Re-reading costs one request covering every camera, and it happens
		on a proxy thread, where blocking holds up nothing except the
		picture being decided on anyway.
		"""
		now = time.time()
		if (now - self._connected_checked > CONNECTED_TTL
				and self.api.is_configured()):
			self._connected_checked = now
			try:
				self.store.merge(self.api.list_cameras())
				self.store.save()
			except Exception as exc:  # noqa: BLE001
				# Keep the stored answer: a console that cannot be reached
				# is handled further along, by the problem card.
				self.log.debug("could not re-read the camera list: %s", exc)
		return self.store.is_connected(camera_id)

	def _problem_card(self, camera_id):
		"""A card for a camera the console calls connected but will not serve.

		An unreachable console and a quality tier that refuses to enable
		both arrive here. Neither is the camera's fault and neither can be
		explained on a television, so the card says only that there is no
		picture -- which is at least the truth, and better than a channel
		that sits black with nothing to read.
		"""
		from . import cards
		if not cards.available():
			return None
		return cards.offline_card(
			self.store.name_for(camera_id), message=_("NO SIGNAL"))

	def _rotate_frame(self):
		"""The picture behind the rotation entry.

		Barely ever seen: selecting that channel is what starts the zap
		hook, and the hook changes channel away from it within a tenth of
		a second. It matters when the hook is not there -- the plugin not
		running, or enigma2 refusing the callback -- and then this is the
		difference between a channel that explains itself and a channel
		that does nothing.
		"""
		from . import cards
		if not cards.available():
			return None
		if self._rotation_entry() is None:
			return None
		return cards.message_card(_("Rotation"))

	def _grid_frame(self):
		"""One picture of every selected camera.

		Snapshots are fetched here, on a proxy thread, and cached for a
		couple of seconds: the grid updates about as fast as the cameras
		can be asked, and a slow console stalls nothing because the
		previous frame is simply shown again.
		"""
		from . import cards
		if not cards.available():
			return None
		now = time.time()
		if now - self._grid_fetched < GRID_INTERVAL and self._grid_cache:
			return self._grid_cache
		self._grid_fetched = now
		tiles = []
		for camera_id in self.store.enabled_ids():
			name = self.store.name_for(camera_id)
			image = None
			if self.store.is_connected(camera_id):
				try:
					image = self.api.get_snapshot(camera_id)
				except Exception as exc:  # noqa: BLE001
					self.log.debug("no snapshot for %s: %s", camera_id, exc)
			tiles.append((name, image))
		if not tiles:
			self._grid_cache = cards.message_card(_("No cameras selected"))
		else:
			self._grid_cache = cards.grid_card(tiles)
		return self._grid_cache

	def camera_entries(self):
		"""``[(camera_id, label, reference), ...]`` for the selected cameras.

		One place builds these, because the rotation channel zaps to the
		very references the bouquet file holds: built twice they would
		drift, and a reference that differs by one character is a channel
		that plays nothing with no sign of why.
		"""
		from . import bouquet as bouquet_module
		from . import servicerefs
		config = settings_module.get_config()
		port = config.bouquet_port.value
		cameras = [
			(cid, self.store.name_for(cid)) for cid in self.store.enabled_ids()]
		labels = bouquet_module.labels_for(cameras)
		entries = []
		for (camera_id, _name), label in zip(cameras, labels):
			token = self.store.token_for(camera_id)
			url = "http://127.0.0.1:%d/cam/%s" % (port, token)
			entries.append(
				(camera_id, label, servicerefs.build_pip_reference(url, label)))
		return entries

	def _reference_for_camera(self, camera_id):
		"""The bouquet reference of one camera's channel, or None."""
		for entry_id, _label, reference in self.camera_entries():
			if entry_id == camera_id:
				return reference
		return None

	def _rotation_entry(self):
		"""``(label, reference)`` for the rotation channel, or None.

		Absent with fewer than two cameras: an entry that behaves exactly
		like the one above it is clutter, and in a channel list clutter is
		the whole problem.
		"""
		from . import servicerefs
		from .player.tsproxy import ROTATE_PATH
		config = settings_module.get_config()
		if not config.bouquet_rotate.value:
			return None
		if len(self.store.enabled_ids()) < 2:
			return None
		label = _("Rotation")
		url = "http://127.0.0.1:%d%s" % (config.bouquet_port.value, ROTATE_PATH)
		return label, servicerefs.build_pip_reference(url, label)

	def bouquet_entries(self):
		"""``[(label, reference), ...]`` for the bouquet file."""
		from . import servicerefs
		config = settings_module.get_config()
		port = config.bouquet_port.value
		entries = [
			(label, reference)
			for _cid, label, reference in self.camera_entries()]
		if entries and config.bouquet_grid.value:
			# Last, so it does not come between the cameras when paging.
			label = _("All cameras")
			url = "http://127.0.0.1:%d/grid" % port
			entries.append((label, servicerefs.build_pip_reference(url, label)))
		rotation_entry = self._rotation_entry()
		if entries and rotation_entry is not None:
			entries.append(rotation_entry)
		self.store.save()
		return entries

	# -- rotation as a channel --------------------------------------------

	def start_zap_rotation(self):
		"""Watch what the receiver plays, so the rotation channel works.

		Everything that decides anything lives in :mod:`zaprotation`, which
		knows nothing about enigma2. What is here is the two things only
		enigma2 can do: say what is playing, and change it.
		"""
		from .zaprotation import ZapRotation
		config = settings_module.get_config()
		entry = self._rotation_entry()
		if not config.bouquet.value or entry is None:
			self.stop_zap_rotation()
			return
		self._zap_rotation = ZapRotation(
			self.rotation, self._reference_for_camera,
			entry_reference=entry[1],
			interval=int(config.rotate_interval.value))
		if self._zap_hooked:
			return
		try:
			self.session.nav.event.append(self._service_started)
		except Exception as exc:  # noqa: BLE001
			self.log.error("could not watch what is playing: %s", exc)
			self._zap_rotation = None
			return
		self._zap_hooked = True
		self.log.info("rotation channel armed, every %ss",
			config.rotate_interval.value)

	def stop_zap_rotation(self):
		"""Let go of the zap hook and the timer."""
		self._zap_rotation = None
		self._disarm_zap_timer()
		if not self._zap_hooked:
			return
		try:
			self.session.nav.event.remove(self._service_started)
		except Exception as exc:  # noqa: BLE001
			self.log.debug("could not unhook from zapping: %s", exc)
		self._zap_hooked = False

	def _service_started(self, event=None):
		"""Called by enigma2 whenever it starts playing something."""
		try:
			from enigma import iPlayableService
			if event is not None and event != iPlayableService.evStart:
				return
		except ImportError:  # pragma: no cover - only off the receiver
			pass
		zapper = self._zap_rotation
		if zapper is None:
			return
		target = zapper.observe(self._playing_reference(), time.time())
		if zapper.running:
			self._arm_zap_timer()
		else:
			self._disarm_zap_timer()
		if target:
			self._zap_to(target)

	def _playing_reference(self):
		try:
			service = self.session.nav.getCurrentlyPlayingServiceReference()
		except Exception as exc:  # noqa: BLE001
			self.log.debug("could not read what is playing: %s", exc)
			return ""
		return service.toString() if service else ""

	def _zap_tick(self):
		zapper = self._zap_rotation
		if zapper is None or not zapper.running:
			self._disarm_zap_timer()
			return
		target = zapper.tick(time.time())
		if target:
			self._zap_to(target)

	def _arm_zap_timer(self):
		if self._zap_timer is None:
			self._zap_timer = eTimer()
			self._zap_timer.callback.append(self._zap_tick)
		self._zap_timer.start(ZAP_TICK_MS, False)

	def _disarm_zap_timer(self):
		if self._zap_timer is not None:
			self._zap_timer.stop()

	def _zap_to(self, reference):
		"""Change channel, out of enigma2's own event callback."""
		self._zap_target = reference
		if self._zap_once is None:
			self._zap_once = eTimer()
			self._zap_once.callback.append(self._do_zap)
		self._zap_once.start(ZAP_DELAY_MS, True)

	def _do_zap(self):
		reference, self._zap_target = self._zap_target, None
		if not reference:
			return
		try:
			from enigma import eServiceReference
			self.session.nav.playService(eServiceReference(reference))
		except Exception as exc:  # noqa: BLE001
			self.log.error("could not change to the next camera: %s", exc)
			if self._zap_rotation is not None:
				self._zap_rotation.stop()
			self._disarm_zap_timer()

	def sync_bouquet(self):
		"""Rewrite an existing bouquet so the settings are reflected in it.

		Which entries the bouquet holds is a setting, so leaving the file
		alone makes those settings look broken: switched on, nothing
		happens, and the menu entry that would write the file is exactly
		the thing in the plugin browser that this bouquet exists to spare
		people. Only an existing bouquet is rewritten -- adjusting an
		option is not a request to put the cameras in the channel list.
		"""
		import os
		from . import bouquet as bouquet_module
		if not settings_module.get_config().bouquet.value:
			return
		if not os.path.exists(bouquet_module.bouquet_path()):
			return
		self.write_bouquet()

	def write_bouquet(self):
		"""Create or refresh the bouquet. Returns ``(ok, message)``."""
		from . import bouquet as bouquet_module
		entries = self.bouquet_entries()
		if not entries:
			return False, _("No cameras are selected.")
		try:
			with open(bouquet_module.bouquet_path(), "w") as handle:
				handle.write(bouquet_module.build_bouquet(entries))
			index_path = bouquet_module.index_path()
			try:
				with open(index_path, "r") as handle:
					existing = handle.read()
			except (OSError, IOError):
				existing = ""
			with open(index_path, "w") as handle:
				handle.write(bouquet_module.add_to_index(existing))
		except (OSError, IOError) as exc:
			self.log.error("could not write the bouquet: %s", exc)
			return False, _("Could not write the bouquet: %s") % exc
		self._reload_bouquets()
		# The rotation entry has just appeared or gone; either way the
		# hook's idea of which reference means "rotate" is now stale.
		self.start_zap_rotation()
		self.log.info("bouquet written with %d camera(s)", len(entries))
		return True, _("Bouquet created with %d camera(s).") % len(entries)

	def remove_bouquet(self):
		"""Take the bouquet out of the channel list again."""
		from . import bouquet as bouquet_module
		import os
		try:
			index_path = bouquet_module.index_path()
			try:
				with open(index_path, "r") as handle:
					existing = handle.read()
			except (OSError, IOError):
				existing = ""
			with open(index_path, "w") as handle:
				handle.write(bouquet_module.remove_from_index(existing))
			try:
				os.remove(bouquet_module.bouquet_path())
			except OSError:
				pass
		except (OSError, IOError) as exc:
			self.log.error("could not remove the bouquet: %s", exc)
			return False, _("Could not remove the bouquet: %s") % exc
		self._reload_bouquets()
		return True, _("Bouquet removed.")

	def _reload_bouquets(self):
		"""Make enigma2 notice the file without a restart."""
		try:
			from enigma import eDVBDB
			db = eDVBDB.getInstance()
			db.reloadBouquets()
			db.reloadServicelist()
		except Exception as exc:  # noqa: BLE001
			self.log.error("could not reload the channel list (%s); "
				"a restart will pick it up", exc)

	# -- motion, reported by Protect ---------------------------------------

	def start_webhook(self):
		"""Listen for Protect's alarm webhooks, if configured."""
		config = settings_module.get_config()
		if not config.webhook.value or self._webhook is not None:
			return
		from .webhook import WebhookServer, generate_token
		if not config.webhook_token.value:
			config.webhook_token.value = generate_token()
			config.webhook_token.save()
			settings_module.save()
		server = WebhookServer(
			port=config.webhook_port.value,
			token=config.webhook_token.value,
			on_motion=self._motion_reported,
			on_learn=self._learn_captured,
			log=self.log)
		try:
			server.start()
		except Exception as exc:  # noqa: BLE001
			# Most likely the port is taken. Say so rather than failing
			# silently, since nothing else would hint at it.
			self.log.error("could not listen for motion on port %s: %s",
				config.webhook_port.value, exc)
			return
		self._webhook = server

	def stop_webhook(self):
		if self._webhook is not None:
			self._webhook.stop()
			self._webhook = None

	def webhook_urls(self):
		"""The URLs to paste into Protect, one per selected camera."""
		config = settings_module.get_config()
		token = config.webhook_token.value
		if not token:
			return []
		from .webhook import motion_url
		host = self._own_address()
		port = self._webhook.port if self._webhook else config.webhook_port.value
		return [
			(self.store.name_for(cid), motion_url(host, port, token, cid))
			for cid in self.store.enabled_ids()]

	def _own_address(self):
		"""This box's address as the console will see it.

		Asked of the routing table by opening a UDP socket towards the
		console -- no packet is sent, but the kernel picks the source
		address it would use, which is exactly the one Protect must call.
		"""
		import socket
		config = settings_module.get_config()
		target = (config.host.value or "").replace("https://", "").strip("/")
		target = target.split(":")[0] or "8.8.8.8"
		sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		try:
			sock.connect((target, 443))
			return sock.getsockname()[0]
		except OSError:
			return "<receiver-ip>"
		finally:
			sock.close()

	def _learn_captured(self, entry):
		"""A call arrived on the learn route; it is already on disk.

		Nothing is decided here: the capture exists so the payload can be
		read once with tools/configure.py --learn-show, before any rule for
		extracting the camera is written.
		"""
		self.log.info("captured a learn call (%s, %d bytes of body)",
			entry.get("method"), len(entry.get("body") or ""))

	def _motion_reported(self, camera_id):
		"""Called from the webhook's own thread -- hand over to the main loop.

		The task runner already marshals between the two, so this reuses
		machinery that is tested rather than inventing a second path.
		"""
		self.runner.run(
			lambda: camera_id, on_success=self._show_motion_camera,
			name="motion")

	def _show_motion_camera(self, camera_id):
		config = settings_module.get_config()
		if camera_id not in self.store.enabled_ids():
			self.log.info("motion on %s, which is not selected", camera_id)
			return
		if not self.corner_running():
			if config.motion_action.value != "show":
				return
			ok, message = self.start_corner()
			if not ok:
				self.log.error("could not show the corner on motion: %s", message)
				return
		if not self.rotation.select(camera_id):
			return
		# Hold this camera briefly so rotation does not slide off it.
		self._motion_until = time.time() + int(config.motion_hold.value)
		self.rotation.pause()
		if self._pip is not None and self._pip.active:
			self._pip._play_current()
		elif self._overlay is not None:
			self._overlay.next_camera()
		self.log.info("showing camera %s after motion", camera_id)
		self._arm_motion_release()

	def _arm_motion_release(self):
		if getattr(self, "_motion_timer", None) is None:
			self._motion_timer = eTimer()
			self._motion_timer.callback.append(self._release_motion_hold)
		self._motion_timer.start(2000, False)

	def _release_motion_hold(self):
		if time.time() < self._motion_until:
			return
		self._motion_timer.stop()
		self.rotation.resume()

	# -- the PiP key ------------------------------------------------------

	def pip_key_long(self):
		"""Long press: bring the corner up, or step to the next camera."""
		if self.corner_running():
			self.next_camera()
			return True, ""
		self._close_foreign_pip()
		return self.start_corner()

	def pip_key_short(self):
		"""Short press: normal PiP, or switch everything off.

		Anything showing -- our corner or the box's own PiP -- is cleared;
		otherwise this is the box's ordinary PiP button.
		"""
		if self.corner_running():
			self.stop_corner()
			return True, ""
		if self._foreign_pip_shown():
			self._close_foreign_pip()
			return True, ""
		infobar = self._infobar()
		if infobar is None:
			return False, _("Picture in Picture is not available here.")
		infobar.showPiP()
		return True, ""

	def _infobar(self):
		try:
			from Screens.InfoBar import InfoBar
			return InfoBar.instance
		except (ImportError, AttributeError):
			return None

	def _foreign_pip_shown(self):
		"""True when a PiP is up that is not ours."""
		return (not self.corner_running()
			and getattr(self.session, "pipshown", False))

	def _close_foreign_pip(self):
		"""Hand the decoder back by closing the box's own PiP.

		Our corner borrows session.pip and refuses to start while something
		else holds it, so the long press would otherwise do nothing at all
		when the user already has PiP open.
		"""
		if not self._foreign_pip_shown():
			return
		infobar = self._infobar()
		if infobar is not None:
			try:
				infobar.showPiP()  # toggles it off
				return
			except Exception as exc:  # noqa: BLE001
				self.log.error("could not close the existing PiP: %s", exc)
		# Last resort, mirroring what InfoBar does itself.
		if hasattr(self.session, "pip"):
			del self.session.pip
		self.session.pipshown = False

	def apply_key_binding(self):
		"""Put our two actions on the box's PiP key, or take them off again.

		Driven by the ``pip_key`` setting. The index ButtonSetup uses for a
		plugin depends on what else is registered, so it is recomputed here
		rather than stored.
		"""
		from . import buttonbinding
		config = settings_module.get_config()
		try:
			from Components.config import config as e2config
			from Screens.ButtonSetup import BUTTON_SETUP_KEYS
			from Plugins.Plugin import PluginDescriptor
			from Components.PluginComponent import plugins
		except ImportError as exc:
			self.log.error("cannot reach the button setup (%s)", exc)
			return False

		holder = getattr(e2config.misc, "ButtonSetup", None)
		if holder is None:
			self.log.error("this image has no button setup to bind to")
			return False

		assignments = {}
		for _label, name, _default in BUTTON_SETUP_KEYS:
			element = getattr(holder, name, None)
			if element is not None:
				assignments[name] = element.value

		pip_key = buttonbinding.find_pip_key(assignments)
		short_name = buttonbinding.short_key(pip_key)
		long_name = buttonbinding.long_key(pip_key)
		if not pip_key:
			self.log.error("found no PiP key to bind to")
			return False

		if not config.pip_key.value:
			# Only clear entries that are ours, never the user's own.
			cleared = 0
			for name in (short_name, long_name):
				element = getattr(holder, name, None)
				if element is not None and buttonbinding.PLUGIN_PATH in element.value:
					element.value = ""
					element.save()
					cleared += 1
			if cleared:
				settings_module.save()
				self.log.info("released %s/%s", short_name, long_name)
			return True

		entries = []
		for plugin in sorted(
				plugins.getPlugins([
					PluginDescriptor.WHERE_PLUGINMENU,
					PluginDescriptor.WHERE_EXTENSIONSMENU,
					PluginDescriptor.WHERE_EVENTINFO,
					PluginDescriptor.WHERE_BUTTONSETUP]),
				key=lambda item: item.name):
			path = plugin.path or ""
			index = path.rfind("Plugins")
			entries.append((plugin.name, path[index:] if index >= 0 else ""))

		from .plugin import KEY_LONG_NAME, KEY_SHORT_NAME
		wanted = buttonbinding.desired_assignments(
			entries, pip_key, KEY_SHORT_NAME, KEY_LONG_NAME)
		if not wanted:
			self.log.error("could not work out the button setup entries")
			return False

		for name, action in wanted.items():
			element = getattr(holder, name, None)
			if element is None:
				self.log.error("this image has no %s key", name)
				return False
			element.value = action
			element.save()
		settings_module.save()
		self.log.info("PiP key bound: %s -> %s, %s -> %s",
			short_name, wanted.get(short_name), long_name, wanted.get(long_name))
		return True

	def next_camera(self):
		self.note_camera_shown()
		if self._overlay is not None:
			self._overlay.next_camera()
		elif self._pip is not None and self._pip.active:
			self._pip.next_camera()
		else:
			self.rotation.next()

	def shutdown(self):
		"""Release everything. Called when the session ends."""
		self.stop_webhook()
		self.stop_bouquet_proxy()
		self.stop_corner()
		self.runner.shutdown()
		DisplayManager.instance = None
