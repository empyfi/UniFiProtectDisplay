# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [semantic versioning](https://semver.org/).

## [0.1.0] - 2026-10-14

First release.

### Added
- Camera discovery through the UniFi Protect Integration API, including
  enabling each camera's RTSP stream, so no stream URL has to be copied by
  hand.
- Snapshot corner: camera stills over live television, on any receiver, with
  no extra packages.
- Live-video corner: RTSP remuxed to MPEG-TS by ffmpeg and played through the
  PiP decoder, on boxes that have a second decoder.
- Fullscreen playback through GStreamer (4097) or exteplayer3 (5002), chosen
  automatically from what the box has installed.
- Rotation through several cameras, with manual skip and pause.
- Runtime capability detection, which disables the live-video corner and says
  why rather than failing later.
- German translation.
