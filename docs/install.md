# Installing and setting up

## 1. Check what your box can do

Run this over SSH or telnet before installing. It decides which corner mode
you will be able to use, and it is worth knowing up front rather than after.

```bash
opkg update; opkg list-installed | grep -E "gstreamer1.0-plugins-good-(rtsp|rtpmanager)"; which ffmpeg; ls -l /proc/stb/vmpeg/1/dst_left; cat /proc/stb/info/boxtype
```

What the answers mean:

| Result | Consequence |
|---|---|
| `/proc/stb/vmpeg/1/dst_left` exists | the box has a second video decoder, so the live-video corner is possible |
| no such file | snapshot corner only — this cannot be worked around |
| `ffmpeg` found | the live-video corner can remux RTSP |
| `gstreamer1.0-plugins-good-rtsp` installed | GStreamer can open `rtsp://` URLs |

Install what is missing:

```bash
opkg install ffmpeg gstreamer1.0-plugins-good-rtsp gstreamer1.0-plugins-good-rtpmanager
```

Optionally, for a more reliable RTSP player than GStreamer on these images:

```bash
opkg install enigma2-plugin-systemplugins-serviceapp exteplayer3
```

The plugin detects serviceapp and prefers exteplayer3 automatically.

## 2. Create a Protect API key

Needs UniFi Protect **5.3 or newer**.

1. Open UniFi Protect.
2. Settings (gear) → **Control Plane** → **Integrations**.
3. Create an API key and copy it — it is shown only once.

The key authenticates against `/proxy/protect/integration/v1/` on the console.
You do not need to enable RTSP per camera: the plugin does that for you
through the API when a camera is first played.

### A word on where the key is stored

Enigma2 keeps every setting in `/etc/enigma2/settings` as plain text and has
no key store. `ConfigPassword` censors the key on screen but does not encrypt
it at rest. Anyone with filesystem access to the receiver — SSH, FTP, a pulled
flash chip — can read it. That is a property of enigma2, not a choice this
plugin makes, but it should inform how much the key is allowed to do in
Protect.

## 3. Install the plugin

```bash
opkg install enigma2-plugin-extensions-unifi-protect-display_*_all.ipk
```

Restart enigma2 afterwards, so the plugin is registered.

From a machine with SSH access to the box, `tools/deploy.py` does the probe,
the build, the install and the restart in one go, and `tools/configure.py`
sets everything up without typing an API key on a remote control:

```bash
pip install paramiko
python tools/deploy.py <box-ip>
python tools/configure.py <box-ip> --console <protect-ip> --api-key <key> \
    --all-cameras --bouquet --bouquet-rotate
```

## 4. Set it up

**Menu → Plugins → UniFi Protect Display**

1. **Console address** — the IP or hostname, e.g. `192.168.1.1`. `https://`
   is assumed.
2. **API key** — paste the key from step 2.
3. Leave **Verify the TLS certificate** off. A UniFi console presents a
   self-signed certificate; with verification on, every request fails.
4. Press **Yellow** (*Test connection*). You should see the Protect version.
5. Press **Blue** (*Cameras*), then **Green** to fetch the list. Press **OK**
   on each camera you want. New cameras start switched off deliberately.
   **Yellow** and **Blue** reorder them, and that order is the rotation order.
6. Switch on **Put the cameras in the channel list**. This is the part
   everybody else in the house will use; the rest is for whoever set the
   box up. See step 5 below.
7. Back in setup, choose a **Corner mode**:
   - *Snapshots* — works everywhere, a picture every second or two
   - *Live video* — real motion, needs the second decoder and ffmpeg; if
     unavailable, the screen says which part is missing
8. Save with **Green**.

## 5. The channel list

With *Put the cameras in the channel list* switched on, write the bouquet
from the plugin's own menu (**Menu → Plugins → UniFi Protect Display**). A
bouquet called **UniFi Protect** appears at the end of the channel list,
holding up to four kinds of entry:

| Entry | What it plays |
|---|---|
| **Cam 1**, **Cam 2**, … | one camera each, live video, numbered because Protect lets two cameras share a name |
| **All cameras** | every selected camera at once as stills, laid out to fill the frame |
| **Rotation** | the cameras in turn, live video, by changing channel on a timer |
| **Rotation (stills)** | the cameras in turn as stills; the camera changes instantly |

The last three are optional and each has its own setting. The two rotating
entries are the same idea traded off differently: **Rotation** gives the
camera's own video but every change is a service restart, so each camera
arrives after however long its stream takes to open; **Rotation (stills)**
changes camera instantly and shows roughly one picture a second, about a
second behind the world. Changing channel is how you leave either one.

A camera the console cannot reach plays a black card naming it and saying
**OFFLINE**, rather than a channel that simply fails. So does a console that
cannot be reached at all. That matters most for whoever did not set this up
and has no other way to tell an unplugged camera from a broken plugin.

Two settings shape the rotating entries, both in setup: **Change camera
every** is how long each camera gets, and **Refresh every** is how often the
picture is renewed on the stills entry. Each appears once the entry that
uses it is switched on.

## 6. Motion

Protect's API cannot report motion, so the direction is reversed: Protect's
Alarm Manager calls the receiver. Switch on *React to motion reported by
Protect*, then print the addresses to paste into Protect:

```bash
python tools/configure.py <box-ip> --webhook-urls
```

In Protect: **Alarm Manager → New Alarm**, one per camera — trigger
**Motion**, scope that camera, action **Webhook → Custom Webhook**, method
GET, and paste the matching URL. The camera is then brought up when it sees
movement, and held there briefly so rotation does not slide away.

## 7. Day-to-day use

From the extensions menu (usually the blue button on the remote):

- **UniFi Protect: corner** — toggle the corner on or off
- **UniFi Protect: fullscreen** — one camera full screen
  - **OK** or **▶** next camera, **◀** previous
  - **Yellow** pause/resume rotation
  - **EXIT** returns to what you were watching
- **UniFi Protect: next camera** — skip ahead without opening anything

*Start the corner automatically* in setup brings it up a few seconds after the
receiver has finished starting.

## Troubleshooting

**"The API key was rejected."**
The key is wrong, was revoked, or the console is older than Protect 5.3.
Create a new key under Control Plane → Integrations.

**"The UniFi console did not answer."**
Wrong address, or the receiver cannot reach the console. Check from the box
— these images have no `curl`, so use Python:

```bash
python3 -c "import ssl,urllib.request as u; c=ssl._create_unverified_context(); \
r=u.Request('https://CONSOLE/proxy/protect/integration/v1/meta/info', \
headers={'X-API-KEY':'YOUR_KEY'}); print(u.urlopen(r,context=c,timeout=10).read())"
```

**Snapshot corner shows nothing.**
Check the plugin's own log, `/tmp/unifiprotectdisplay.log`. If snapshots
arrive but nothing is drawn, the JPEG may not be reaching tmpfs — check free
space in `/tmp`.

**Fullscreen plays nothing, but the URL works in VLC.**
Almost always a missing GStreamer RTSP plugin. Install
`gstreamer1.0-plugins-good-rtsp` and `gstreamer1.0-plugins-good-rtpmanager`,
or install serviceapp and exteplayer3 and set **Player** to *exteplayer3*.

**Playback fails with a TLS error.**
Switch on **Use unencrypted RTSP**. This rewrites the URL to port 7447 without
TLS. Some Protect versions do not serve that port; if it still fails, the
encrypted stream is the only option and the box's ffmpeg or GStreamer build is
the limiting factor.

**Live-video corner: "Incorrect type service for PiP!"**
Enigma2 could not put the stream on the second decoder. Usually the codec:
Protect's "Enhanced" encoding is H.265, which many PiP decoders cannot handle.
Set **Stream quality** to *low* or *medium*, which is H.264 on most cameras.

**Live-video corner: nothing happens and the log is quiet.**
Check that ffmpeg is actually running while the corner is up:

```bash
ps w | grep ffmpe[g]
```

If it is not, the proxy never got a connection, which points at the service
reference rather than at ffmpeg. Turn on **Write debug messages to the log**
in setup and look for `[UnifiProtectDisplay]` lines.

**A channel in the bouquet plays nothing.**
Look in `/tmp/unifiprotectdisplay.log`. A camera the console reports as down
should log `is offline; serving a card` within a second or so; if instead
nothing is logged, the proxy never got the connection, which points at the
bouquet file rather than at the plugin. Check that the port in
`/etc/enigma2/userbouquet.unifiprotect.tv` matches the one the log says the
proxy is listening on, and rewrite the bouquet from the plugin menu if not.

**Collecting a useful log.**

The plugin keeps its own, capped, in tmpfs. It has to: enigma2's stdout goes
to `/dev/console` on these images, so `print` output reaches no file at all.

```bash
tail -f /tmp/unifiprotectdisplay.log
```

or from a machine with SSH access, which also survives the restart:

```bash
python tools/deploy.py <box-ip> --log-only
```

Switch on *Write debug messages to the log* in setup for more. The plugin
never writes the API key or a stream token to the log, so its lines are safe
to post in a forum. Enigma2's own lines are not necessarily — check before
pasting.
