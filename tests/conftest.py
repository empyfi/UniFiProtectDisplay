# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Make the plugin importable off the box.

On a receiver the package lives under enigma2's plugin path, which is already
on sys.path. Here we add the directory that *contains* the package, so tests
import it exactly as enigma2 does: ``UnifiProtectDisplay.servicerefs``.

Only modules free of enigma2 imports can be tested this way, which is the
reason the plugin keeps its logic in such modules and confines ``from enigma
import ...`` to the screens.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXTENSIONS = os.path.join(
	_HERE, os.pardir, "src", "usr", "lib", "enigma2", "python",
	"Plugins", "Extensions")

sys.path.insert(0, os.path.abspath(_EXTENSIONS))
