# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Single source of truth for the plugin version.

``build.py`` and ``configure.ac`` read PLUGIN_VERSION from here, so the version
is never written down in two places that can drift apart.
"""

PLUGIN_NAME = "UniFi Protect Display"
PLUGIN_VERSION = "0.1.3"
PLUGIN_PACKAGE = "enigma2-plugin-extensions-unifi-protect-display"
