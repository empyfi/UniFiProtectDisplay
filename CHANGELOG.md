# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [semantic versioning](https://semver.org/).

## [0.6.0] - 2026-10-28

The channel list became the point of the plugin. Everything before this was
reached through the plugin browser, which is fine for whoever set the box up
and too much for anybody else in the house.

### Added
- **A bouquet.** The cameras appear in the channel list as ordinary
  channels, numbered, reachable with the remote everyone already uses. The
  entries are type 1 references pointing at a loopback proxy on a fixed
  port, because Protect serves rtsps:// with SRTP and GStreamer's rtspsrc
  does not do SRTP. Confirmed playing on a receiver.
- **An "all cameras" entry**: every selected camera at once as stills, laid
  out 2x2, 3x3 or 4x4 as the count requires, each tile labelled.
- **An offline card.** A camera the console cannot reach serves a black
  frame naming it and saying so, rather than a channel that simply fails -
  whoever is watching may have no way to tell an unplugged camera from a
  broken plugin.
- **Motion.** Protect's Alarm Manager calls the receiver when a camera sees
  movement, and that camera is brought up. The Protect API cannot report
  motion itself: no events endpoint, a silent event WebSocket, and the
  private API rejects an API key. A learn route records what Protect sends,
  for working out whether one URL could replace the per-camera ones.
- The remote's **PiP key** drives the camera corner: long press shows it and
  steps to the next camera, short press keeps the box's own
  Picture-in-Picture and switches everything off.
- The plugin's **own menu** in the plugin browser, and a screen showing the
  webhook addresses to paste into Protect.
- `tools/configure.py` sets the plugin up over SSH, including the API key
  and camera selection, from a real keyboard.

### Changed
- Offline cameras keep their place and are labelled instead of being
  skipped. Hiding them conceals that a camera exists and is down, which is
  the opposite of what a surveillance display is for.
- The corner takes its position and size from the box's own PiP window, so
  arranging Picture-in-Picture once arranges both.

### Fixed
- The corner stayed black because ffmpeg never started: the RTSP demuxer in
  these images rejects `-rw_timeout` and exits before reading a byte.
- Only the video stream is taken. A UniFi camera offers two audio tracks,
  one of them Opus, which MPEG-TS cannot carry.
- Fullscreen played nothing, because GStreamer's rtspsrc does not do SRTP.
- The corner no longer overwrites `config.av.pip`, the user's own
  Picture-in-Picture geometry.
- The plugin keeps its own log: enigma2's stdout goes to `/dev/console` on
  this image, so `print` output reaches no file at all.

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
