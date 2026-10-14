# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [semantic versioning](https://semver.org/).

## [0.2.3] - 2026-10-14

Everything below was found on a real receiver (GigaBlue UHD Quad 4K Pro,
openATV 7.6) and fixed there.

### Added
- The remote's PiP key drives the camera: long press shows the corner and
  steps to the next camera, short press keeps the box's own
  Picture-in-Picture and switches everything off when something is showing.
  The key is found by looking for whichever one the image bound to
  `Infobar/showPiP`.
- The plugin's own menu, reachable through the plugin browser. openATV has
  no menu entry for PiP geometry at all, and the extensions entry that
  would open one does not appear on every box, so this is the dependable
  route to every action.
- A window editor, so position and size can be adjusted from the plugin.
- `tools/deploy.py` and `tools/configure.py`: install and set up over SSH,
  including the API key and camera selection, from a real keyboard.
- Cameras the console reports as offline are marked in the list and stepped
  over when switching. The list is re-checked in the background whenever a
  camera reaches the screen.

### Fixed
- The corner stayed black because ffmpeg never started: the RTSP demuxer in
  the ffmpeg builds on these images rejects `-rw_timeout` and exits before
  reading a byte.
- Only the video stream is taken now. A UniFi camera offers two audio
  tracks, one of them Opus, which MPEG-TS cannot carry.
- Fullscreen played nothing because GStreamer's rtspsrc does not do SRTP,
  and Protect serves `rtsps://` with `?enableSrtp`. Both modes now go
  through the same ffmpeg remux.
- The corner no longer overwrites `config.av.pip` -- the user's own
  Picture-in-Picture position and size. It takes its geometry from there
  and writes nothing back.
- Nothing reached any log, because enigma2's stdout goes to `/dev/console`
  on this image. The plugin keeps its own capped log in tmpfs.

### Changed
- The corner is exactly the size and position of the box's own PiP window.
  The camera fills it by default; keeping the proportions is an option,
  with the consequence stated in the setting.

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
