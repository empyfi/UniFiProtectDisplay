#!/bin/sh
# Regenerate the autotools build system.
set -e
mkdir -p m4
autoreconf --install --force --verbose
echo "Now run: ./configure && make && make DESTDIR=... install"
