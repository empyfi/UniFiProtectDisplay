# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Single source of truth for the plugin version.

``build.py`` reads PLUGIN_VERSION from here and writes it into the package,
overwriting whatever ``CONTROL/control`` happens to say.

``configure.ac`` cannot read it -- autoconf wants a literal -- so it keeps
its own copy, and the two drifted apart for seven releases without anyone
noticing, because the .ipk path never looks at it and the OpenEmbedded path
is not what this is built with day to day. ``tests/test_build.py`` now fails
when they disagree, which is the only thing that was ever going to catch it.
"""

PLUGIN_NAME = "UniFi Protect Display"
PLUGIN_VERSION = "0.8.0"
PLUGIN_PACKAGE = "enigma2-plugin-extensions-unifi-protect-display"
