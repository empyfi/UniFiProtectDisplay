#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build, upload and install the plugin on a receiver over SSH.

	python tools/deploy.py 192.168.140.2

What it does, in order: probe what the box can do, build the .ipk if it is
missing, upload it, install it with opkg, restart Enigma2, and report what
the plugin logged on the way up.

Credentials
-----------
The password is never stored in this file and never passed on the command
line, where it would end up in the shell history and in the process list.
It is read, in order of preference, from:

  1. an SSH key -- ``--key ~/.ssh/id_ed25519_gbquad``, or the agent;
  2. the ``E2_PASSWORD`` environment variable;
  3. an interactive prompt that does not echo.

A key is worth the one-time setup: it survives a password change, can be
revoked on its own, and keeps the password out of every shell this ever
runs in.

	ssh-copy-id -i ~/.ssh/id_ed25519_gbquad.pub root@<box>
"""

import argparse
import getpass
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, os.pardir)
DIST = os.path.join(ROOT, "dist")
PACKAGE_PREFIX = "enigma2-plugin-extensions-unifi-protect-display"
REMOTE_DIR = "/tmp"
PLUGIN_DIR = ("/usr/lib/enigma2/python/Plugins/Extensions/UnifiProtectDisplay")

# Everything the capability probe needs to answer, in one round trip.
PROBE = r"""
echo "## box";        cat /proc/stb/info/boxtype 2>/dev/null || echo unknown
echo "## image";      head -1 /etc/issue 2>/dev/null
echo "## pip_node";   ls /proc/stb/vmpeg/1/dst_left >/dev/null 2>&1 && echo yes || echo no
echo "## decoders";   ls -d /proc/stb/vmpeg/* 2>/dev/null | tr '\n' ' '; echo
echo "## ffmpeg";     command -v ffmpeg || echo no
echo "## gst_rtsp";   ls /usr/lib/gstreamer-1.0/libgstrtsp.so >/dev/null 2>&1 && echo yes || echo no
echo "## serviceapp"; ls -d /usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp >/dev/null 2>&1 && echo yes || echo no
echo "## exteplayer3"; command -v exteplayer3 || echo no
echo "## python";     (python3 -V 2>&1 || python -V 2>&1) | head -1
echo "## tmpfree";    df -h /tmp 2>/dev/null | awk 'NR==2 {print $4}'
"""


def log(message):
	print(message, flush=True)


def fail(message, code=1):
	print("error: %s" % message, file=sys.stderr, flush=True)
	sys.exit(code)


# -- local build ----------------------------------------------------------

def newest_ipk():
	if not os.path.isdir(DIST):
		return None
	candidates = [
		os.path.join(DIST, name) for name in os.listdir(DIST)
		if name.startswith(PACKAGE_PREFIX) and name.endswith(".ipk")]
	return max(candidates, key=os.path.getmtime) if candidates else None


def build_if_needed(force=False):
	existing = newest_ipk()
	if existing and not force:
		return existing
	log("building the package...")
	subprocess.check_call([sys.executable, os.path.join(ROOT, "build.py")])
	built = newest_ipk()
	if not built:
		fail("build.py did not produce an .ipk")
	return built


# -- connection -----------------------------------------------------------

def connect(host, user, port, key_path, timeout):
	try:
		import paramiko
	except ImportError:
		fail("paramiko is not installed: pip install paramiko")

	client = paramiko.SSHClient()
	client.load_system_host_keys()
	# A receiver is reinstalled and reflashed often enough that a changed
	# host key is routine rather than suspicious, and this only ever talks
	# to an address the user named on the command line.
	client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

	attempts = []
	if key_path:
		attempts.append(("key %s" % key_path, {
			"key_filename": os.path.expanduser(key_path),
			"look_for_keys": False, "allow_agent": False}))
	else:
		attempts.append(("ssh agent or default key", {
			"look_for_keys": True, "allow_agent": True}))

	password = os.environ.get("E2_PASSWORD")
	if password:
		attempts.append(("E2_PASSWORD", {
			"password": password, "look_for_keys": False,
			"allow_agent": False}))

	last_error = None
	for description, kwargs in attempts:
		try:
			client.connect(
				hostname=host, port=port, username=user, timeout=timeout,
				**kwargs)
			log("connected to %s as %s (%s)" % (host, user, description))
			return client
		except Exception as exc:  # noqa: BLE001 - paramiko raises many types
			last_error = exc

	# Everything non-interactive failed; ask, without echoing.
	try:
		password = getpass.getpass("password for %s@%s: " % (user, host))
	except (EOFError, KeyboardInterrupt):
		fail("no credentials available (last error: %s)" % last_error)
	try:
		client.connect(
			hostname=host, port=port, username=user, password=password,
			timeout=timeout, look_for_keys=False, allow_agent=False)
	except Exception as exc:  # noqa: BLE001
		fail("could not log in: %s" % exc)
	log("connected to %s as %s (password)" % (host, user))
	return client


def run(client, command, check=True, quiet=False):
	"""Run one command, return ``(status, stdout, stderr)``."""
	stdin, stdout, stderr = client.exec_command(command, timeout=120)
	out = stdout.read().decode("utf-8", "replace")
	err = stderr.read().decode("utf-8", "replace")
	status = stdout.channel.recv_exit_status()
	if not quiet and out.strip():
		for line in out.strip().split("\n"):
			log("    %s" % line)
	if status != 0 and check:
		fail("`%s` failed (exit %d)\n%s" % (command, status, err.strip()))
	return status, out, err


# -- steps ----------------------------------------------------------------

def probe(client):
	log("\n== what this box can do ==")
	_, out, _ = run(client, PROBE, quiet=True)
	values = {}
	key = None
	for line in out.split("\n"):
		line = line.strip()
		if line.startswith("## "):
			key = line[3:].strip()
			values[key] = ""
		elif key and line:
			values[key] = (values[key] + " " + line).strip()

	for label, name in (
			("box", "box"), ("image", "image"),
			("second video decoder", "pip_node"), ("ffmpeg", "ffmpeg"),
			("gstreamer rtsp", "gst_rtsp"), ("serviceapp", "serviceapp"),
			("exteplayer3", "exteplayer3"), ("python", "python"),
			("free in /tmp", "tmpfree")):
		log("    %-22s %s" % (label, values.get(name, "?")))

	has_pip = values.get("pip_node") == "yes"
	has_ffmpeg = values.get("ffmpeg", "no") != "no"
	log("")
	if has_pip and has_ffmpeg:
		log("    -> live-video corner: available")
	else:
		missing = []
		if not has_pip:
			missing.append("no second video decoder")
		if not has_ffmpeg:
			missing.append("ffmpeg missing (opkg install ffmpeg)")
		log("    -> live-video corner: NOT available (%s)" % "; ".join(missing))
		log("    -> snapshot corner and fullscreen are unaffected")
	if values.get("gst_rtsp") != "yes" and values.get("exteplayer3", "no") == "no":
		log("    -> no RTSP player found; install either")
		log("       gstreamer1.0-plugins-good-rtsp + ...-rtpmanager")
		log("       or enigma2-plugin-systemplugins-serviceapp + exteplayer3")
	return values


def upload(client, local_path):
	remote_path = "%s/%s" % (REMOTE_DIR, os.path.basename(local_path))
	size = os.path.getsize(local_path)
	log("\n== uploading ==")
	log("    %s (%.1f KiB) -> %s" % (
		os.path.basename(local_path), size / 1024.0, remote_path))
	sftp = client.open_sftp()
	try:
		sftp.put(local_path, remote_path)
		# Trust nothing: confirm the byte count actually arrived.
		if sftp.stat(remote_path).st_size != size:
			fail("upload is truncated; aborting before install")
	finally:
		sftp.close()
	return remote_path


def install(client, remote_path):
	log("\n== installing ==")
	# --force-reinstall so repeat deployments of the same version work,
	# which is the normal case while developing.
	status, out, err = run(
		client, "opkg install --force-reinstall %s" % remote_path,
		check=False)
	if status != 0:
		fail("opkg failed:\n%s\n%s" % (out.strip(), err.strip()))
	run(client, "rm -f %s" % remote_path, check=False, quiet=True)

	# A stale .pyc beside a replaced .py is a classic "my fix did not take
	# effect" on these boxes.
	run(client, "find %s -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; true"
		% PLUGIN_DIR, check=False, quiet=True)

	status, out, _ = run(
		client, "ls %s/plugin.py && opkg list-installed | grep unifi-protect"
		% PLUGIN_DIR, check=False, quiet=True)
	if status != 0:
		fail("the plugin files are not where they should be after install")
	for line in out.strip().split("\n"):
		log("    %s" % line)


def restart_enigma(client, wait):
	log("\n== restarting Enigma2 ==")
	log("    the picture will go away for a moment")
	# init 4 / init 3 is the supported way; killing enigma2 outright can
	# leave the box without a running GUI.
	run(client, "init 4", check=False, quiet=True)
	time.sleep(3)
	run(client, "init 3", check=False, quiet=True)
	log("    waiting %ds for it to come back..." % wait)
	deadline = time.time() + wait
	# Poll rather than sleeping blindly, so a fast box is not waited out.
	while time.time() < deadline:
		status, out, _ = run(
			client, "pidof enigma2 >/dev/null && echo up || echo down",
			check=False, quiet=True)
		if "up" in out:
			time.sleep(4)  # let it finish loading plugins
			log("    Enigma2 is running again")
			return True
		time.sleep(3)
	log("    Enigma2 did not come back in time -- check the box")
	return False


def show_log(client):
	log("\n== what the plugin logged ==")
	status, out, _ = run(
		client,
		"grep -h UnifiProtectDisplay /home/root/logs/* /tmp/*.log 2>/dev/null "
		"| tail -25", check=False, quiet=True)
	text = out.strip()
	if text:
		for line in text.split("\n"):
			log("    %s" % line)
	else:
		log("    nothing yet. Enigma2 only loads the plugin when it is opened,")
		log("    or at session start when autostart is on. Open")
		log("    Menu > Plugins > UniFi Protect Display and run this again")
		log("    with --log-only.")


# -- entry point ----------------------------------------------------------

def main(argv=None):
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("host", help="receiver address, e.g. 192.168.140.2")
	parser.add_argument("--user", default="root")
	parser.add_argument("--port", type=int, default=22)
	parser.add_argument(
		"--key", help="private key file; otherwise the agent, then "
		"E2_PASSWORD, then a prompt")
	parser.add_argument(
		"--rebuild", action="store_true", help="rebuild the .ipk first")
	parser.add_argument(
		"--no-restart", action="store_true",
		help="install without restarting Enigma2")
	parser.add_argument(
		"--probe-only", action="store_true",
		help="only report what the box can do, change nothing")
	parser.add_argument(
		"--log-only", action="store_true",
		help="only show the plugin's log lines")
	parser.add_argument("--wait", type=int, default=90)
	parser.add_argument("--timeout", type=float, default=10.0)
	args = parser.parse_args(argv)

	client = connect(args.host, args.user, args.port, args.key, args.timeout)
	try:
		if args.log_only:
			show_log(client)
			return 0
		probe(client)
		if args.probe_only:
			return 0
		package = build_if_needed(args.rebuild)
		remote = upload(client, package)
		install(client, remote)
		if not args.no_restart:
			restart_enigma(client, args.wait)
			show_log(client)
		log("\ndone. Setup is under Menu > Plugins > UniFi Protect Display.")
	finally:
		client.close()
	return 0


if __name__ == "__main__":
	sys.exit(main())
