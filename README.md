# UniFi Protect Display

An Enigma2 plugin that puts UniFi Protect cameras on your receiver: in a
corner of the screen over live television, or full screen, one camera or
several in rotation.

Cameras are found through the Protect Integration API, which also switches on
each camera's RTSP stream — no stream URLs have to be enabled per camera in
the Protect UI and copied across by hand.

Tested against openATV 7.x, Python 3.

---

## What the corner can and cannot do

This is the part worth understanding before installing, because it decides
which mode you get.

Enigma2 will only place a service on the second (PiP) decoder if that service
implements `setTarget(1)`. Checking the sources rather than guessing:

| Service type | Player | `setTarget()` |
|---|---|---|
| 4097 | GStreamer, `eServiceMP3` | `return -1` — ["not implemented (yet)"](https://github.com/openatv/enigma2/blob/master/lib/service/servicemp3.h#L186) |
| 5001 / 5002 | serviceapp / exteplayer3 | [`return -1`](https://github.com/OpenVisionE2/serviceapp/blob/develop/src/serviceapp/serviceapp.h#L119) |
| 1 | DVB, `eDVBServicePlay` | [implemented](https://github.com/openatv/enigma2/blob/master/lib/service/servicedvb.cpp#L2156) |

`PictureInPicture.playService()` rewrites `4097:` to `1:` and then requires
`setTarget(1, True)` to succeed. The type‑1 stream path reads through
`eHttpStream`, which speaks HTTP and expects MPEG‑TS. **An RTSP camera
therefore cannot go into the PiP window directly** — not a limitation of this
plugin, but of what enigma2 can be asked to do.

So the plugin offers two corner modes:

**Snapshots** (default). JPEGs from the Protect API, decoded with `ePicLoad`
and drawn into an `ePixmap` over whatever is playing. No decoder involved, so
it works on every receiver alongside live TV, with nothing extra installed.
Roughly one picture every one to two seconds — stills, not video.

**Live video** (optional). `ffmpeg -c copy` remuxes RTSP into MPEG‑TS and
serves it on loopback; the PiP decoder plays that as a type‑1 service:

```
camera --RTSP--> ffmpeg -c copy --MPEG-TS--> 127.0.0.1 --> type 1 --> PiP
```

`-c copy` repackages rather than re-encodes, which is the only reason this is
affordable on a set-top box. It needs a box with a second video decoder and
the `ffmpeg` package. The plugin probes for both at startup and, if either is
missing, disables the mode and says which one — rather than letting it fail
later with enigma2's own unhelpful popup.

Fullscreen playback has none of these constraints: it uses the main decoder
with GStreamer or exteplayer3, and works wherever the box can decode the
camera's codec.

## Requirements

- Enigma2 with Python 3 (openATV 7.x and comparable images)
- UniFi Protect **5.3 or newer**, for the Integration API
- A Protect API key: Protect → Control Plane → Integrations

Optional, detected at runtime:

```bash
opkg install gstreamer1.0-plugins-good-rtsp gstreamer1.0-plugins-good-rtpmanager
```

for RTSP through GStreamer, and

```bash
opkg install ffmpeg
```

for the live-video corner. `enigma2-plugin-systemplugins-serviceapp` enables
exteplayer3, which handles RTSP more reliably than GStreamer on these images
and is preferred automatically when present.

## Install

Download the `.ipk` from the releases page and:

```bash
opkg install enigma2-plugin-extensions-unifi-protect-display_0.1.0_all.ipk
```

Then restart enigma2. Setup lives under **Menu → Plugins → UniFi Protect
Display**.

Building from source needs nothing but Python:

```bash
python build.py
```

## Using it

In setup, enter the console address and the API key, press **Yellow** to test
the connection, then **Blue** to open the camera list. Press **Green** there
to fetch the cameras, and **OK** to select the ones you want. New cameras
arrive switched off on purpose — adding a camera in Protect should not
silently put it on your television.

From the extensions menu (usually the blue button):

- **UniFi Protect: corner** — switch the corner on or off
- **UniFi Protect: fullscreen** — one camera full screen; OK or ◀ ▶ to change
  camera, Yellow to pause rotation, EXIT to return to television
- **UniFi Protect: next camera** — skip ahead

## Motion

Protect's API cannot report motion: the Integration API has no events
endpoint, its event WebSocket completes the handshake and then delivers
nothing, and the private API the web interface uses rejects an API key. All
three were measured against a live console.

Protect's **Alarm Manager** can call out, though, so the direction is
reversed — the console tells the receiver, which needs no connection kept
alive. Switch on *React to motion reported by Protect*, then print the URLs:

```bash
python tools/configure.py <box-ip> --webhook
python tools/configure.py <box-ip> --webhook-urls
```

In Protect: **Alarm Manager → New Alarm**, one per camera — trigger
**Motion**, scope that camera, action **Webhook → Custom Webhook**, method
GET, and paste the matching URL.

When motion is reported the camera is brought up, switching the corner on if
it is off, and held there briefly so rotation does not slide away. The URL
carries a token so a stray request cannot flip the television; it is not a
strong secret, and regenerating it invalidates URLs already pasted.

## Notes and limits

- **H.265 cameras.** Protect's "Enhanced" encoding is H.265. Older boxes
  cannot decode it on the PiP decoder; choose the low or medium stream tier,
  which is H.264 on most models.
- **RTSPS and TLS.** Protect serves `rtsps://…:7441/…?enableSrtp` with a
  self-signed certificate. Where ffmpeg or GStreamer cannot complete that
  handshake, switch on *Use unencrypted RTSP*, which rewrites the URL to port
  7447. That port does not exist on every Protect version.
- **The API key is stored in plain text.** Enigma2 keeps every setting in
  `/etc/enigma2/settings` as text and has no key store; `ConfigPassword` only
  censors the display. Anyone with filesystem access to the receiver can read
  it. Scope the key accordingly in Protect.
- **TLS verification is off by default.** With it on, every request to a
  stock console fails, because the certificate is self-signed.
- The plugin never logs the API key or a stream URL's token.

## Development

```bash
python -m pytest           # the test suite, no receiver needed
python -m pyflakes src/
python tools/extract_strings.py   # regenerate po/UnifiProtectDisplay.pot
python build.py                   # produce dist/*.ipk
```

To put a build on a receiver, including a report of what that box can do:

```bash
pip install paramiko
python tools/deploy.py 192.168.1.50            # probe, build, install, restart
python tools/deploy.py 192.168.1.50 --probe-only   # change nothing
python tools/deploy.py 192.168.1.50 --log-only     # just read the plugin's log
```

It authenticates with an SSH key, the agent, `E2_PASSWORD`, or a prompt, in
that order. A key is worth the one-time setup — it survives a password change
and keeps the password out of your shell history:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519_box.pub root@192.168.1.50
```

Everything that can be tested off the box is kept free of `enigma` imports —
API client, service-reference escaping, geometry, rotation, capability
probing, the camera store and the orchestration layer. Enigma2 is confined to
the screens. See [docs/architecture.md](docs/architecture.md).

## Licence

GPL-2.0-or-later. See [LICENSE](LICENSE).
