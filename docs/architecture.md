# Architecture

## Where the plugin is actually used

Through the channel list. Everything else -- setup, the camera list, the
corner, fullscreen -- is reached through the plugin browser, which is fine
for whoever set the box up and too much for anybody else in the house. A
bouquet is the most familiar thing a receiver has, so the cameras are in
it as ordinary channels and the rest of the plugin exists to keep them
there.

That decides more of the design than it looks. A reference sitting in a
channel list has to work tomorrow, which rules out the per-session tokens
the corner uses. An entry either plays or it does not, which is why a
camera that is down is a picture saying so rather than a dead channel --
and why a picture has to be encoded as video at all.

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
     ┌──────────────┬┴──────────────┬────────────────┬──────────────┐
     │              │               │                │              │
  ui/setup.py   render/          player/          player/        webhook.py
  ui/cameras.py  snapshot_overlay  fullscreen      pip_remux      (motion in)
  ui/mainmenu.py render/           player/         player/
  ui/webhookinfo offline_overlay   tsproxy         imagestream
     │              │               │                │              │
     └──────────────┴───────────────┴────────────────┴──────────────┘
                     │
                controller.py         what to ask for, and when
                     │
     ┌───────────────┼───────────────┬──────────────┬───────────────┐
  protect/api    camerastore     rotation       capabilities    bouquet.py
  protect/models  geometry        async_util     servicerefs     cards.py
  settings.py     logger.py       zaprotation    slideshow       buttonbinding
```

`player/tsproxy.py` straddles the line: it imports no enigma2 and is tested
on a laptop against a real socket, but it is the thing enigma2 connects to,
so it sits with the players rather than below the controller.

Everything below `controller.py` is free of `enigma` imports and covered by
tests that run on a laptop. Everything above it touches enigma2 and is kept as
thin as it can be: the screens hold layout and key handling, not decisions.

The two rotating channels are the clearest case. Both ways of rotating have
a module below the line holding every decision -- `zaprotation.py` and
`slideshow.py` -- and neither has a timer or a clock of its own; they are
told what time it is. What is left above the line is the timer itself and,
for the zapping one, the two calls only enigma2 can make: say what is
playing, and change it.

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
an IPC mechanism. ffmpeg starts when enigma2 opens the connection and dies when
it closes it, so switching the corner off is enough to stop the camera traffic.

There are two proxies and they differ in exactly one way. The corner's takes
any free port and invents a path token per session, so a URL that leaks into
a log stops working at the next restart. The bouquet's keeps a fixed port and
tokens stored in the camera file, because a reference in a channel list has
to work tomorrow. Binding to loopback is what makes the second acceptable:
the URL is useless to anyone not already on the receiver, and a Protect
stream token never appears in it.

The order of the checks on a camera route is deliberate and was measured.
Whether a camera is offline is decided from the token alone, before the
stream URL is resolved, because resolving it costs one or two Protect
requests — six to eleven seconds of black screen for a camera that was never
going to play. A stream that cannot be resolved, and a remux that produces
no bytes at all, both fall back to a card rather than a dead channel.

`Content-Type: video/mp2t` is deliberate: `eHttpStream` treats
`application/text`, `audio/x-mpegurl`, `audio/mpegurl` and `application/m3u`
as playlists and would try to read a URL out of the body. The status line must
also have three tokens, because `eHttpStream` parses it with
`sscanf("%99s %d %99s")` and requires all three.

### `bouquet.py` and the channel list
The bouquet file is plain text, so writing it is string handling and lives
below the line with its own tests. Two things there are less obvious than
they look.

`bouquets.tv` lists the user's own bouquets, and losing one of those would
be a far worse bug than anything else in this plugin, so the index is
rewritten by preserving every line and appending ours only if it is not
already there. Appending also puts the cameras at the end of the channel
list, where they displace nothing.

The entries are type 1 -- the DVB service path -- pointing at the loopback
proxy. They cannot point at Protect directly, because it serves `rtsps://`
with SRTP and GStreamer's rtspsrc does not do SRTP. Given a proxy is needed
anyway, type 1 is strictly better than 4097: it reads HTTP MPEG-TS
natively, it is the only type Picture-in-Picture accepts, and the channel
list treats it as an ordinary service, so one reference works for zapping,
for PiP and in the corner.

### `cards.py` and `player/imagestream.py`
A bouquet entry either plays or it does not, so the things that are not a
camera feed -- an offline camera, the grid, a slide -- still have to arrive
as video. They are drawn with PIL and fed to ffmpeg as a JPEG sequence,
coming out as the same MPEG-TS a real camera does, so nothing downstream
needs a special case.

Encoding these is affordable where encoding live video is not: the frames
are nearly static, so `ultrafast` with `stillimage` tuning at a few frames
a second costs a few percent of one core. A grid of live video would need a
decoder per camera and an encoder the box does not have.

The first picture is written several times back to back. ffmpeg emits
nothing until it has worked out what its input is, and it works that out by
reading frames; at two frames a second that inspection was five seconds of
black screen on a receiver, against one second with the priming and a short
input probe. The cost is that the stream then runs behind the world by
frames divided by frame rate -- two seconds at 2 fps, one at 4 -- which is
why the rotating stills channel runs at 4.

### `zaprotation.py` and `slideshow.py`
Two ways to play the cameras in turn, and they are opposites.

`zaprotation` changes channel on a timer, so the picture is the camera's own
video, decoded by the same decoder, with nothing lost to a second encode.
Every change is a service restart. What it has to decide is harder than it
sounds: telling our own zap from the viewer's, noticing that the viewer has
left, and never re-zapping the channel that is already playing. The zap
itself is deferred by a one-shot timer, because the decision is taken inside
enigma2's own service-started callback and calling `playService()` from
there is re-entering what called us.

`slideshow` never changes channel: one service plays throughout and what
goes into it is a snapshot of whichever camera's turn it is. The camera
changes instantly and an offline camera shows its card in its turn. It is
asked for a frame several times a second and answers most of those with the
picture it already has; what is economised is the request to the console,
not the answer. It must always hand back a picture, because the proxy asks
a route for a frame to find out whether the route exists at all -- answering
None served a 404 to the second viewer, which only the receiver found.

### `webhook.py`
Protect's API cannot report motion: the Integration API has no events
endpoint, its event WebSocket completes the handshake and then delivers
nothing, and the private API the web interface uses rejects an API key. All
three were measured against a live console. Protect's Alarm Manager can call
out, though, so the direction is reversed and the receiver listens instead of
polling -- which also means nothing has to be kept alive.

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
`timer_factory`/`thread_factory` on `TaskRunner`, `popen` on `TsProxy` and
`ImageStreamer`. `ZapRotation` and `SlideRotation` take the clock as an
argument rather than reading one, so rotation is tested at whatever moment
the test likes instead of in real time.

A few tests deliberately go further than unit scope: `test_tsproxy.py` runs a
real HTTP server on loopback and reads the raw bytes off a socket, because the
contract that matters there is the one `eHttpStream` sees on the wire.
`test_translations.py` checks that format specifiers match between `msgid` and
`msgstr` — a mismatch raises at runtime, on the box, often in an error path
that only runs when something has already gone wrong.
