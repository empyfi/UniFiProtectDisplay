# Architecture

## The constraint everything else follows from

Enigma2 is single-threaded and its second video decoder is reachable only
through one narrow door. Those two facts shape the whole design.

**Single-threaded.** The GUI, zapping, recordings and every plugin share one
main loop. An HTTP call made from it blocks the user's television until it
returns — on an NVR that is powered off, for the whole timeout. Every network
call in this plugin therefore runs on a worker thread and is delivered back on
the main loop (`async_util.py`).

**The PiP door.** `Screens/PictureInPicture.playService()` rewrites a `4097:`
reference to `1:` and then requires `setTarget(1, True)` to return 0. Only
`eDVBServicePlay` implements it; `eServiceMP3` and `eServiceApp` both return
-1. The type‑1 stream path reads through `eHttpStream`, which speaks HTTP and
expects MPEG‑TS. RTSP cannot pass through that door, so either the picture
stops being video (snapshots) or something has to turn RTSP into HTTP MPEG‑TS
first (the remux proxy). The plugin does both and lets the box decide which
is available.

## Layers

```
                 plugin.py            PluginDescriptors, nothing else
                     │
                 manager.py           one instance per session; owns the state
                     │
     ┌───────────────┼────────────────┬──────────────────┐
     │               │                │                  │
  ui/setup.py   render/            player/            player/
  ui/cameras.py  snapshot_overlay   fullscreen         pip_remux
     │               │                │                  │
     └───────────────┴────────────────┴──────────────────┘
                     │
                controller.py         what to ask for, and when
                     │
     ┌───────────────┼───────────────┬──────────────┐
  protect/api    camerastore     rotation       capabilities
  protect/models  geometry        async_util     servicerefs
```

Everything below `controller.py` is free of `enigma` imports and covered by
tests that run on a laptop. Everything above it touches enigma2 and is kept as
thin as it can be: the screens hold layout and key handling, not decisions.

`localisation.py` sits to the side of this. It binds the plugin's own gettext
domain, so modules import `_` explicitly instead of relying on the global one
enigma2 installs for its own catalogue.

## Module notes

### `protect/api.py`
Five endpoints of the Protect Integration API, over stdlib `urllib` — openATV
does not guarantee `requests`, and a dependency for five calls would make the
package harder to install than the plugin is to use. The transport is
injectable, which is how the whole client is tested without a console.

Errors are typed by what the user must do about them, not by HTTP status:
`ProtectAuthError` (make a new key), `ProtectUnavailableError` (retrying may
help), `ProtectNotFoundError`, `ProtectProtocolError` (retrying will not
help — the console's API differs from what we target).

### `servicerefs.py`
Service reference strings, and the escaping they need. Enigma2 writes them
with `encode()` and reads them with `urlDecode()` (`lib/service/service.cpp`,
`lib/base/estring.cpp`); `encode_component` mirrors `encode()` exactly: only
`:`, `%` and control characters are escaped. Two consequences that are easy to
get wrong by guessing — `?`, `&`, `=` and `/` must *not* be escaped, so
Protect's `?enableSrtp` survives; and `%` must become `%25`, or a URL already
containing a percent escape is silently corrupted. A mistake here produces a
reference that parses cleanly and plays nothing, which is why it has its own
module and its own tests.

### `async_util.py`
Work runs on a short-lived daemon thread; results come back through a queue
that one enigma2 timer drains on the main loop. Cancellation matters as much
as dispatch: a snapshot arriving after its overlay closed must not be painted
into a destroyed widget, so every submission returns a handle that can be
cancelled, and `shutdown()` cancels everything.

### `capabilities.py`
Filesystem probes, cached, injectable. `/proc/stb/vmpeg/1/dst_left` is the
box's own statement that it has a second decoder — `eVideoWidget` drives the
hardware scaler through those nodes. Blockers are returned as *codes*, not
sentences, so this module needs no gettext and the UI can translate them.

### `geometry.py`
Two coordinate systems, which is the whole reason it exists. Skin space is
whatever the active skin declares (1280×720, 1920×1080). PiP space is a fixed
720×576 grid — `Screens/PictureInPicture.py` hardcodes `MAX_X`/`MAX_Y` and
stores `config.av.pip` in it. Feeding skin coordinates to `session.pip.move()`
puts the window far off screen. Sizes are expressed as a percentage of width
so one setting means the same thing in both.

### `player/tsproxy.py`
ffmpeg plus a loopback HTTP server. Binds to 127.0.0.1 only — an
unauthenticated camera feed must not appear on the LAN because a plugin needed
an IPC mechanism — and gives each camera a random path token per session, so a
URL that leaks into a log or a bouquet stops working at the next restart.
ffmpeg starts when enigma2 opens the connection and dies when it closes it, so
switching the corner off is enough to stop the camera traffic.

`Content-Type: video/mp2t` is deliberate: `eHttpStream` treats
`application/text`, `audio/x-mpegurl`, `audio/mpegurl` and `application/m3u`
as playlists and would try to read a URL out of the body. The status line must
also have three tokens, because `eHttpStream` parses it with
`sscanf("%99s %d %99s")` and requires all three.

### `player/pip_remux.py`
Borrows `session.pip` and `session.pipshown` using exactly the sequence
`InfoBarGenerics.showPiP` uses, so enigma2's own state stays consistent and
its PiP button still works — it simply closes the camera. Because enigma2 can
tear the PiP down without telling us, a watchdog notices and runs the cleanup.
`config.av.pip` is the user's own geometry and is saved before being
overwritten, then put back on stop.

### `render/snapshot_overlay.py`
`ePicLoad` reads from a file, not from memory, so each JPEG is written to
tmpfs first — two filenames alternating, because a decode runs on its own
thread and overwriting the file it is reading would hand it half an image.
tmpfs and not flash: a receiver writing a JPEG to NAND twice a second would
wear it out.

`setPara` is called with `aspect_ratio = 0`, which means "fill the destination
exactly". That is correct here because the rectangle was already computed at
the camera's aspect ratio, so nothing is stretched.

## Testing

`tests/` runs without enigma2 and without a UniFi console. The seams that make
that possible are all constructor arguments: `transport` on `ProtectApi`,
`exists`/`environ` on `Capabilities`, `reader`/`writer` on `CameraStore`,
`timer_factory`/`thread_factory` on `TaskRunner`, `popen` on `TsProxy`.

A few tests deliberately go further than unit scope: `test_tsproxy.py` runs a
real HTTP server on loopback and reads the raw bytes off a socket, because the
contract that matters there is the one `eHttpStream` sees on the wire.
`test_translations.py` checks that format specifiers match between `msgid` and
`msgstr` — a mismatch raises at runtime, on the box, often in an error path
that only runs when something has already gone wrong.
