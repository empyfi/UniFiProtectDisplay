# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Putting the cameras in the channel list.

Why this matters more than the plugin's own screens
---------------------------------------------------
Everything else in this plugin is reached through the plugin browser, which
is fine for whoever set the box up and too much for everybody else in the
house. A bouquet is the most familiar thing a receiver has: press the channel
list, pick "UniFi Protect", page through the cameras with up and down. There
is nothing to learn, and it works with the remote people already use.

Why the entries are type 1
--------------------------
They cannot point at Protect directly: it serves ``rtsps://`` with SRTP, and
GStreamer's rtspsrc does not do SRTP -- measured, not assumed. So they point
at the plugin's own loopback proxy, which remuxes RTSP into MPEG-TS over
HTTP.

Given that, type 1 (the DVB service path) is strictly better than 4097: it
reads HTTP MPEG-TS natively, it is the only type Picture-in-Picture accepts,
and the channel list treats it as an ordinary service. The same reference
therefore works for zapping, for PiP, and in the plugin's own corner.

For this to hold the proxy needs a fixed port and tokens that survive a
restart, unlike the per-session ones used when only the corner is running --
a reference in a channel list has to keep working tomorrow.
"""

# Where enigma2 keeps bouquets, and the index that lists them.
BOUQUET_DIR = "/etc/enigma2"
BOUQUET_FILE = "userbouquet.unifiprotect.tv"
BOUQUETS_INDEX = "bouquets.tv"

# The line that makes the index point at our bouquet.
INDEX_LINE = ('#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" '
	'ORDER BY bouquet' % BOUQUET_FILE)

DEFAULT_NAME = "UniFi Protect"


def camera_label(index, name, duplicate=False):
	"""What the channel list shows for one camera.

	Numbered because that is how a channel list is read, and because
	Protect lets two cameras share a name -- which they often do straight
	out of the box, leaving entries that cannot be told apart. The real
	name follows when it adds anything.
	"""
	number = "Cam %d" % index
	name = (name or "").strip()
	if not name:
		return number
	if duplicate or name.lower() != number.lower():
		return "%s - %s" % (number, name)
	return number


def labels_for(cameras):
	"""Labels for ``[(id, name), ...]``, numbered from one.

	A name shared by several cameras is still distinguished, because the
	number comes first either way.
	"""
	names = [name for _id, name in cameras]
	counts = {}
	for name in names:
		counts[name] = counts.get(name, 0) + 1
	return [
		camera_label(index, name, duplicate=counts.get(name, 0) > 1)
		for index, (_id, name) in enumerate(cameras, 1)
	]


def build_bouquet(entries, name=DEFAULT_NAME):
	"""Render the bouquet file.

	``entries`` is ``[(label, reference), ...]``. The reference is already
	a complete service reference string, escaped -- building it belongs to
	:mod:`servicerefs`, which is where the escaping rules are pinned down.
	"""
	lines = ["#NAME %s" % name]
	for label, reference in entries:
		lines.append("#SERVICE %s" % reference)
		lines.append("#DESCRIPTION %s" % label)
	return "\n".join(lines) + "\n"


def add_to_index(existing, line=INDEX_LINE):
	"""Return ``bouquets.tv`` with our bouquet listed exactly once.

	Every other line is preserved in order: this file lists the user's own
	bouquets, and losing one of those would be a far worse bug than
	anything else here. Appending puts the cameras at the end of the
	channel list, where they do not displace anything.
	"""
	lines = [item for item in (existing or "").split("\n")]
	# Drop a trailing empty line so the file does not grow one each time.
	while lines and not lines[-1].strip():
		lines.pop()
	if any(BOUQUET_FILE in item for item in lines):
		return "\n".join(lines) + "\n"
	lines.append(line)
	return "\n".join(lines) + "\n"


def remove_from_index(existing):
	"""Return ``bouquets.tv`` without our bouquet. Other lines are kept."""
	lines = [
		item for item in (existing or "").split("\n")
		if item.strip() and BOUQUET_FILE not in item
	]
	return "\n".join(lines) + "\n" if lines else ""


def bouquet_path(directory=BOUQUET_DIR):
	return "%s/%s" % (directory.rstrip("/"), BOUQUET_FILE)


def index_path(directory=BOUQUET_DIR):
	return "%s/%s" % (directory.rstrip("/"), BOUQUETS_INDEX)
