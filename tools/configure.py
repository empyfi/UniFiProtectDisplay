#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Configure the plugin on a receiver from a real keyboard.

Entering an API key with a television remote is miserable, so this does the
whole setup over SSH instead:

	python tools/configure.py 192.168.140.2 \
	    --console 192.168.1.1 --api-key XXXX --all-cameras --mode pip

It talks to UniFi Protect from *this* machine first, so a wrong address or a
rejected key is reported here rather than discovered later on the television.
With the key accepted it fetches the camera list, writes both the Enigma2
settings and the plugin's camera file, and restarts Enigma2.

Merge semantics
---------------
Only what you pass is changed. Everything else keeps whatever is on the box,
so adjusting one option later does not quietly reset the rest. ``--reset``
starts from the plugin's defaults instead.

Order of operations
-------------------
Enigma2 rewrites /etc/enigma2/settings in full when it shuts down, so writing
while it runs would be undone a moment later. The script therefore stops
Enigma2, writes, and starts it again. Values written before the plugin has
ever loaded are still picked up: Enigma2 keeps unknown entries and applies
them when a plugin creates the matching config subsection.
"""

import argparse
import getpass
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, os.pardir)
PLUGIN_SRC = os.path.join(
	ROOT, "src", "usr", "lib", "enigma2", "python", "Plugins", "Extensions")
sys.path.insert(0, os.path.abspath(PLUGIN_SRC))

# Reuse the plugin's own client and store, so this script and the plugin can
# never disagree about what the API returns or how the camera file is shaped.
from UnifiProtectDisplay.protect import errors  # noqa: E402
from UnifiProtectDisplay.protect.api import ProtectApi  # noqa: E402

SETTINGS_PATH = "/etc/enigma2/settings"
CAMERAS_PATH = "/etc/enigma2/unifiprotectdisplay_cameras.json"
PREFIX = "config.plugins.unifiprotectdisplay."

# Every setting the plugin understands, with its default and how Enigma2
# serialises it. Booleans are written "True"/"False" -- capitalised, as
# ConfigBoolean.toString produces them.
BOOLEAN = "bool"
PLAIN = "plain"

SETTINGS = {
	"host": (PLAIN, ""),
	"apikey": (PLAIN, ""),
	"verify_ssl": (BOOLEAN, False),
	"quality": (PLAIN, "high"),
	"plain_rtsp": (BOOLEAN, False),
	"player": (PLAIN, "auto"),
	"corner_mode": (PLAIN, "off"),
	"follow_pip": (BOOLEAN, True),
	"corner": (PLAIN, "topright"),
	"corner_width": (PLAIN, "25"),
	"corner_margin": (PLAIN, "2"),
	"snapshot_interval": (PLAIN, "2"),
	"snapshot_high_quality": (BOOLEAN, False),
	"rotate": (BOOLEAN, False),
	"rotate_interval": (PLAIN, "15"),
	"autostart": (BOOLEAN, False),
	"debug": (BOOLEAN, False),
	"proxy_port": (PLAIN, "0"),
}


def log(message=""):
	print(message, flush=True)


def fail(message, code=1):
	print("error: %s" % message, file=sys.stderr, flush=True)
	sys.exit(code)


def to_setting(name, value):
	"""Serialise one value the way Enigma2 would have written it."""
	kind = SETTINGS[name][0]
	if kind == BOOLEAN:
		return "True" if value in (True, "True", "true", "1", "yes", "on") else "False"
	return str(value)


# -- Protect --------------------------------------------------------------

def resolve_api_key(given):
	"""Key from the flag, the environment, or a prompt that does not echo.

	Passing it as a flag is supported because that is what makes this
	copy-pasteable, but it does land in the shell history -- hence the
	environment variable and the prompt as alternatives.
	"""
	if given:
		return given.strip()
	from_env = os.environ.get("PROTECT_API_KEY")
	if from_env:
		return from_env.strip()
	try:
		return getpass.getpass("UniFi Protect API key: ").strip()
	except (EOFError, KeyboardInterrupt):
		fail("no API key given")


def fetch_cameras(console, api_key, verify_ssl):
	api = ProtectApi(console, api_key, verify_ssl=verify_ssl)
	log("checking %s ..." % api.base_url())
	try:
		info = api.meta_info()
	except errors.ProtectAuthError:
		fail("the console rejected this API key. Create a new one under "
			"Control Plane > Integrations (needs Protect 5.3 or newer).")
	except errors.ProtectUnavailableError as exc:
		fail("could not reach the console: %s" % exc)
	except errors.ProtectError as exc:
		fail("%s" % exc)
	version = (info or {}).get("applicationVersion", "?")
	log("  connected, Protect %s" % version)
	try:
		cameras = api.list_cameras()
	except errors.ProtectError as exc:
		fail("could not read the camera list: %s" % exc)
	log("  %d camera(s) found" % len(cameras))
	return cameras


def choose_cameras(cameras, wanted, take_all, interactive):
	"""Work out which cameras to enable.

	``wanted`` matches on name or id, case-insensitively, so the obvious
	thing -- typing the name shown in Protect -- works.
	"""
	if take_all:
		return [c.id for c in cameras]
	if wanted:
		selected = []
		for token in wanted:
			token = token.strip()
			if not token:
				continue
			match = next(
				(c for c in cameras
					if c.id == token or c.name.lower() == token.lower()), None)
			if match is None:
				fail("no camera called %r. Known: %s"
					% (token, ", ".join(c.name for c in cameras)))
			selected.append(match.id)
		return selected
	if not interactive:
		return None  # leave the existing selection alone

	log("\nWhich cameras should be shown?")
	for index, camera in enumerate(cameras, 1):
		state = "" if camera.connected else "  (offline)"
		log("  %2d  %s%s" % (index, camera.name, state))
	log("  Enter numbers separated by commas, 'a' for all, or nothing to skip.")
	try:
		answer = input("  > ").strip()
	except (EOFError, KeyboardInterrupt):
		return None
	if not answer:
		return None
	if answer.lower() in ("a", "all"):
		return [c.id for c in cameras]
	selected = []
	for token in answer.replace(" ", "").split(","):
		if not token:
			continue
		if not token.isdigit() or not 1 <= int(token) <= len(cameras):
			fail("%r is not one of the numbers above" % token)
		selected.append(cameras[int(token) - 1].id)
	return selected


# -- the box --------------------------------------------------------------

def connect(args):
	"""Open the SSH connection, reusing deploy.py's credential handling."""
	import importlib.util
	spec = importlib.util.spec_from_file_location(
		"deploy_module", os.path.join(HERE, "deploy.py"))
	deploy = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(deploy)
	return deploy.connect(args.host, args.user, args.port, args.key, args.timeout)


def run(client, command, check=True):
	stdin, stdout, stderr = client.exec_command(command, timeout=120)
	out = stdout.read().decode("utf-8", "replace")
	err = stderr.read().decode("utf-8", "replace")
	status = stdout.channel.recv_exit_status()
	if status != 0 and check:
		fail("`%s` failed (exit %d)\n%s" % (command, status, err.strip()))
	return status, out, err


def read_remote(client, path):
	status, out, _ = run(client, "cat %s 2>/dev/null" % path, check=False)
	return out if status == 0 else ""


def write_remote(client, path, text):
	"""Upload atomically: write a temporary file, then rename over the target.

	A receiver can lose power at any moment, and half a settings file is far
	worse than the previous one.
	"""
	sftp = client.open_sftp()
	try:
		temp = path + ".new"
		with sftp.open(temp, "w") as handle:
			handle.write(text)
		try:
			sftp.remove(path)
		except IOError:
			pass
		sftp.rename(temp, path)
	finally:
		sftp.close()


def current_settings(client):
	"""Read the plugin's settings as they stand on the box."""
	values = {}
	for line in read_remote(client, SETTINGS_PATH).split("\n"):
		if line.startswith(PREFIX) and "=" in line:
			key, _, value = line.partition("=")
			name = key[len(PREFIX):]
			if name in SETTINGS:
				values[name] = value.strip()
	return values


def merged_settings(client, overrides, reset):
	base = {name: to_setting(name, default)
		for name, (_, default) in SETTINGS.items()}
	if not reset:
		base.update(current_settings(client))
	for name, value in overrides.items():
		base[name] = to_setting(name, value)
	return base


def rewrite_settings(original, values):
	"""Return /etc/enigma2/settings with the plugin's entries replaced.

	Pure, because this edits the file that holds every setting on the box --
	tuners, bouquets, timers, the lot. Dropping an unrelated line here would
	be a far worse bug than anything the plugin itself can do, so the
	behaviour is pinned down by tests: every foreign line is preserved,
	in order, and only keys under our own prefix are touched.
	"""
	kept = [
		line for line in original.split("\n")
		if line and not line.startswith(PREFIX)]
	for name in sorted(values):
		kept.append("%s%s=%s" % (PREFIX, name, values[name]))
	return "\n".join(kept) + "\n"


def write_settings(client, values):
	original = read_remote(client, SETTINGS_PATH)
	if not original:
		fail("%s is empty or unreadable -- is this an Enigma2 box?"
			% SETTINGS_PATH)
	write_remote(client, SETTINGS_PATH, rewrite_settings(original, values))


def write_cameras(client, cameras, enabled_ids):
	"""Write the plugin's camera file, preserving order and names."""
	payload = [
		{"id": c.id, "name": c.name, "enabled": c.id in enabled_ids}
		for c in cameras]
	write_remote(client, CAMERAS_PATH, json.dumps(payload, indent=1))
	return payload


def restart_enigma(client, wait):
	log("\nrestarting Enigma2 (the picture goes away for a moment)")
	run(client, "init 4", check=False)
	# Give it time to shut down and write its own settings before we touch
	# the file; otherwise that write would land on top of ours.
	deadline = time.time() + 30
	while time.time() < deadline:
		_, out, _ = run(
			client, "pidof enigma2 >/dev/null && echo up || echo down",
			check=False)
		if "down" in out:
			break
		time.sleep(2)
	time.sleep(2)
	return True


def start_enigma(client, wait):
	run(client, "init 3", check=False)
	log("  waiting for it to come back ...")
	deadline = time.time() + wait
	while time.time() < deadline:
		_, out, _ = run(
			client, "pidof enigma2 >/dev/null && echo up || echo down",
			check=False)
		if "up" in out:
			time.sleep(4)
			log("  Enigma2 is running again")
			return True
		time.sleep(3)
	log("  Enigma2 did not come back in time -- check the box")
	return False


# -- entry point ----------------------------------------------------------

def build_parser():
	parser = argparse.ArgumentParser(
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("host", help="receiver address, e.g. 192.168.140.2")
	parser.add_argument("--user", default="root")
	parser.add_argument("--port", type=int, default=22)
	parser.add_argument("--key", help="SSH private key for the receiver")
	parser.add_argument("--timeout", type=float, default=10.0)
	parser.add_argument("--wait", type=int, default=90)

	protect = parser.add_argument_group("UniFi Protect")
	protect.add_argument("--console", help="Protect console address")
	protect.add_argument(
		"--api-key", help="Protect API key; also PROTECT_API_KEY, else prompt")
	protect.add_argument(
		"--verify-ssl", action="store_true",
		help="verify the console certificate (it is self-signed, so this "
			"normally fails)")
	protect.add_argument(
		"--cameras", help="comma separated camera names or ids to enable")
	protect.add_argument(
		"--all-cameras", action="store_true", help="enable every camera")
	protect.add_argument(
		"--list", action="store_true",
		help="only list the cameras, change nothing")

	display = parser.add_argument_group("display")
	display.add_argument(
		"--mode", choices=("off", "snapshot", "pip"), help="corner mode")
	display.add_argument(
		"--follow-pip", dest="follow_pip", action="store_true", default=None,
		help="size the corner exactly like the box's own PiP window")
	display.add_argument(
		"--own-corner", dest="follow_pip", action="store_false",
		help="use the plugin's own corner position and width instead")
	display.add_argument(
		"--corner",
		choices=("topright", "topleft", "bottomright", "bottomleft"))
	display.add_argument("--width", type=int, help="corner width in %% (10-60)")
	display.add_argument("--margin", type=int, help="margin in %% (0-10)")
	display.add_argument(
		"--quality", choices=("high", "medium", "low"), help="stream quality")
	display.add_argument(
		"--player", choices=("auto", "gstreamer", "exteplayer3", "gstplayer"))
	display.add_argument(
		"--interval", type=int, help="seconds between snapshots")
	display.add_argument(
		"--rotate", dest="rotate", action="store_true", default=None)
	display.add_argument("--no-rotate", dest="rotate", action="store_false")
	display.add_argument(
		"--rotate-interval", type=int, help="seconds per camera")
	display.add_argument(
		"--plain-rtsp", dest="plain_rtsp", action="store_true", default=None,
		help="use unencrypted RTSP on port 7447")
	display.add_argument(
		"--autostart", dest="autostart", action="store_true", default=None)
	display.add_argument(
		"--no-autostart", dest="autostart", action="store_false")
	display.add_argument(
		"--debug", dest="debug", action="store_true", default=None)

	behaviour = parser.add_argument_group("behaviour")
	behaviour.add_argument(
		"--reset", action="store_true",
		help="start from the plugin's defaults instead of merging")
	behaviour.add_argument(
		"--show", action="store_true",
		help="print the configuration on the box and exit")
	behaviour.add_argument(
		"--dry-run", action="store_true",
		help="show what would be written, change nothing")
	behaviour.add_argument("--no-restart", action="store_true")
	return parser


def collect_overrides(args, api_key):
	"""Only the settings actually given on the command line."""
	overrides = {}
	mapping = (
		("console", "host"), ("verify_ssl", "verify_ssl"), ("mode", "corner_mode"),
		("follow_pip", "follow_pip"),
		("corner", "corner"), ("width", "corner_width"),
		("margin", "corner_margin"), ("quality", "quality"),
		("player", "player"), ("interval", "snapshot_interval"),
		("rotate", "rotate"), ("rotate_interval", "rotate_interval"),
		("plain_rtsp", "plain_rtsp"), ("autostart", "autostart"),
		("debug", "debug"),
	)
	for attribute, setting in mapping:
		value = getattr(args, attribute, None)
		# False is a meaningful value for the switches, so test for None.
		if value is not None and not (attribute == "verify_ssl" and not value):
			overrides[setting] = value
	if api_key:
		overrides["apikey"] = api_key
	return overrides


def show(client):
	values = current_settings(client)
	log("configuration on the box:")
	if not values:
		log("  (nothing set yet)")
	for name in sorted(SETTINGS):
		shown = values.get(name, "(default %s)" % SETTINGS[name][1])
		if name == "apikey" and values.get(name):
			shown = "*** (set)"
		log("  %-22s %s" % (name, shown))
	raw = read_remote(client, CAMERAS_PATH)
	log("\ncameras:")
	try:
		for entry in json.loads(raw):
			log("  [%s] %s" % ("x" if entry.get("enabled") else " ",
				entry.get("name")))
	except ValueError:
		log("  (none yet)")


def main(argv=None):
	args = build_parser().parse_args(argv)

	cameras = []
	api_key = None
	if args.console or args.api_key or args.list or args.all_cameras or args.cameras:
		if not args.console:
			fail("--console is required to talk to Protect")
		api_key = resolve_api_key(args.api_key)
		cameras = fetch_cameras(args.console, api_key, args.verify_ssl)
		if args.list:
			log("")
			for camera in cameras:
				log("  %-36s %s%s" % (
					camera.id, camera.name,
					"" if camera.connected else "  (offline)"))
			return 0

	client = connect(args)
	try:
		if args.show:
			show(client)
			return 0

		overrides = collect_overrides(args, api_key)
		values = merged_settings(client, overrides, args.reset)
		selected = choose_cameras(
			cameras, args.cameras.split(",") if args.cameras else None,
			args.all_cameras, interactive=bool(cameras) and sys.stdin.isatty())

		log("\nsettings to write:")
		for name in sorted(values):
			shown = "*** (set)" if name == "apikey" and values[name] else values[name]
			marker = " <-" if name in overrides else ""
			log("  %-22s %s%s" % (name, shown, marker))
		if selected is not None:
			log("\ncameras to enable: %s" % (", ".join(
				c.name for c in cameras if c.id in selected) or "(none)"))

		if args.dry_run:
			log("\ndry run: nothing was changed")
			return 0

		# Enigma2 rewrites the settings file on shutdown, so it has to be
		# down before we touch it.
		running = not args.no_restart
		if running:
			restart_enigma(client, args.wait)

		write_settings(client, values)
		log("\nwrote %s" % SETTINGS_PATH)
		if selected is not None:
			write_cameras(client, cameras, set(selected))
			log("wrote %s" % CAMERAS_PATH)

		if running:
			start_enigma(client, args.wait)
		else:
			log("\nnot restarting: the new settings take effect after a "
				"restart of Enigma2.")
		log("\ndone.")
	finally:
		client.close()
	return 0


if __name__ == "__main__":
	sys.exit(main())
