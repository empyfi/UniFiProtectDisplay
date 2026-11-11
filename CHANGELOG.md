# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [semantic versioning](https://semver.org/).

## [0.8.0] - 2026-10-28

### Added
- **A "rotation (stills)" entry**: the cameras in turn, the other way
  round from the rotation entry added in 0.7.0. One service plays
  throughout and what goes into it is a snapshot of whichever camera's
  turn it is, so the camera changes instantly instead of costing a
  service restart, and a camera that is down shows its card in its turn
  rather than holding everything up. Both entries can be in the bouquet
  at once; they are a trade of the same coin.

  About one new picture a second -- the snapshot interval decides, 1 to
  30 seconds -- running about a second behind the world. It runs at four
  frames a second rather than the two the cards use: the encoder is
  primed with a fixed number of frames so a zap shows something quickly,
  and that priming is exactly what puts the stream behind, so the higher
  rate halves the lag. Measured on a receiver: a picture 0.6 to 0.9
  seconds after the channel is selected, and the camera changing on
  schedule.

  Switched on in setup, or with `tools/configure.py --bouquet-slides`.

### Changed
- The documentation caught up with the last three releases. `docs/`
  described the plugin as it was before the channel list existed: the
  architecture had no note for the bouquet, the cards or either kind of
  rotation, and both it and `docs/api.md` still said a URL in a bouquet
  file stops working at the next restart, which stopped being true when
  the bouquet started keeping its tokens. `docs/install.md` never
  mentioned the channel list or motion at all, and sent people to log
  files the plugin no longer writes to.
- `configure.ac` had been left at 0.1.0 since the first release. Nothing
  noticed, because `build.py` rewrites `CONTROL/control` for the `.ipk`
  and never looks at `configure.ac` -- but the OpenEmbedded path does,
  and would have built a package claiming to be 0.1.0. A test now fails
  when the versions disagree.

### Fixed
- A camera that comes back is noticed. The flags saying which cameras the
  console can reach were only refreshed by the corner, by the camera list
  screen, or at most every thirty seconds by a channel start -- so a
  camera plugged back in went on playing an OFFLINE card, and worse, the
  rotation channel went on stepping over it. With two cameras that left
  rotation sitting on the other one for as long as the channel stayed on,
  which looks like rotation being broken rather than like a stale flag.
  A channel start now re-reads the list almost always, the grid re-reads
  it as it draws, and the rotation channel keeps it current in the
  background while it runs. The camera store logs a camera changing
  state, so the next time this is asked the log answers it.
- The stills refresh about twice as fast. Everything drawn rather than
  filmed -- the cards, the grid, both rotating entries -- now runs at four
  frames a second instead of two, which halves the priming cushion the
  stream runs behind by, from two seconds to one. The grid also renews its
  snapshots on the refresh interval rather than a hardcoded two seconds,
  and that interval now defaults to one second.

  Honouring the frame rate turned out to be the other half of it. The
  grid's own snapshot requests happen inside the frame loop -- 0.22
  seconds each, measured -- and a whole interval was waited on top, so
  ffmpeg was fed slower than the frame rate it had been told. The wait is
  now whatever is left of the interval. Measured over the loopback on the
  receiver: the grid delivers 500 to 660 KB in six seconds where it
  delivered 426 KB before, and its first byte arrives after 1.0 seconds
  warm, 1.7 cold.

  Measured while doing this, and now in `docs/api.md`: a snapshot takes
  0.18 to 0.39 seconds, and two in a row are byte for byte identical, so
  Protect makes a new picture about every half second. That is the floor;
  asking faster would cost requests and show the same picture.
- Motion no longer restarts a camera that is already on screen. Protect's
  Alarm Manager sends one alarm per movement rather than one per event, so
  somebody standing in front of a camera produces a run of them -- and
  each one asked Protect for a stream again and restarted the decoder, so
  the picture flickered for as long as they stood there. A repeat report
  now only extends how long the camera is kept.
- A camera brought up by motion keeps the screen for a few seconds before
  another camera's motion can take it. Two cameras watching the same path
  report within a second or two of each other, and the screen swapped back
  and forth faster than a stream could open: what you saw was not both
  cameras but neither. After those seconds newer motion wins at once.
- In snapshot corner mode, motion showed the camera *after* the one that
  moved. The code selected the right camera and then called the manual
  *next camera* action, which stepped one past it; with two cameras that
  meant motion always showed the wrong one. PiP mode was unaffected.

  The decision about what a report should do now lives in
  `motionpolicy.py`, below the architecture line and with the clock passed
  in, so these sequences are 25 tests rather than guesses.
- Two settings the channel list depends on were reachable only through the
  corner. *Change camera every* decides how long each camera gets on both
  rotating channels but appeared only when the corner's own rotation was
  on, and *Refresh every* decides how often the stills channel renews its
  picture but appeared only in snapshot corner mode. Somebody using
  nothing but the channel list could not see either one.
- Slides name the camera the way the channel list does, numbered.
  Protect lets two cameras share a name and both on the console this was
  built against are called "G3 Flex", so the bare name told nobody which
  camera was on screen -- which is the one thing a rotating channel has
  to get right.

## [0.7.0] - 2026-10-28

The channel list is the part of this plugin anybody else in the house will
use, so this release is about it being quick and about not having to press
anything.

### Added
- **A "rotation" entry**: the cameras in turn, with nothing to press. It
  works by changing channel on a timer, so the picture is the camera's own
  video rather than anything re-encoded. The price is that each change is
  a service restart, so a camera arrives after however long its stream
  takes to open -- stated in the setting rather than discovered on the
  sofa. Changing channel leaves it, like any other channel. Confirmed on a
  receiver: handing over in 1.2s, stepping every fifteen seconds, and
  stopping dead when the channel was changed by hand.
- The grid and rotation entries can be switched on from the setup screen
  and from `tools/configure.py` (`--bouquet-grid`, `--bouquet-rotate`).
- The README has a section on the channel list, which it had never
  described at all.

### Fixed
- **A camera channel took six to eleven seconds to show anything**, which
  is long enough to read as a channel that does not work -- defeating the
  point of having an offline card. Three separate causes, all measured on
  a receiver, all now about 1.3 seconds:
  - whether a camera was offline was decided only after asking Protect
    where it streams from, so an unreachable camera paid for a round trip
    that could not help it;
  - the stored "connected" flag was never re-read, so a camera unplugged
    since the last camera-list fetch still sent the channel off to remux a
    stream that no longer existed;
  - ffmpeg emits nothing until it has worked out what its input is, and at
    two frames a second that inspection took five seconds of wall clock.
- A stream that cannot be resolved, and a remux that produces not one
  byte, now say so on screen instead of leaving the channel dead.
- `tools/deploy.py` rebuilds when the sources are newer than the last
  `.ipk`. It used to upload whatever was in `dist/`, so an edit made since
  the last build never reached the box -- and the deploy reported success,
  which is the worse of the two possible failures.

### Changed
- The grid no longer keeps a row it has nothing to put in. Two cameras --
  the commonest installation there is -- sat across the top of a 2x2 with
  the bottom half of the television black, which reads as a broken picture
  rather than a sparse one. Each tile now keeps the shape of a camera
  picture whether or not there is a picture for it, so an offline tile is
  the same size as a live one, and the name sits on the picture instead of
  floating below it.
- The channel-list settings take effect on a bouquet that already exists,
  instead of waiting for somebody to find the menu entry that rewrites the
  file.

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
