# The UniFi Protect Integration API, as this plugin uses it

Reference for the five endpoints the plugin calls, written down because the
official documentation is a JavaScript application that is awkward to quote
and because the local-console form differs from the cloud examples it shows.

Everything here was verified against
[uilibs/uiprotect](https://github.com/uilibs/uiprotect), the de-facto
reference client, and Ubiquiti's
[developer documentation](https://developer.ui.com/protect/).

## Basics

- **Available from** Protect 5.3. Earlier consoles have only the private
  bootstrap API, which needs the account password and changes shape between
  releases; this plugin does not use it.
- **Base path** `/proxy/protect/integration` on the console itself. The
  published examples route through `api.ui.com/v1/connector/consoles/{id}/…`,
  which is the same API reached through Ubiquiti's cloud; the local path is
  what a plugin on the LAN wants.
- **Authentication** the header `X-API-KEY`. Created in Protect under
  Control Plane → Integrations.
- **TLS** the console presents a self-signed certificate. Verification has to
  be off, or every request fails. See `protect/api.py` for how that is scoped.

## Endpoints

### `GET /v1/meta/info`

Cheapest call there is, which makes it the right one behind a "test
connection" button. Returns the Protect version:

```json
{ "applicationVersion": "6.0.53" }
```

### `GET /v1/cameras`

Every camera the key can see. The plugin reads `id` and `name`, and treats
`isConnected` or `state` as the online flag when present. This is also the
call the channel list makes to decide whether to serve a camera or an
OFFLINE card, at most once every thirty seconds, because one request answers
for every camera at once. Protect adds fields
between releases, so parsing is lenient: unknown keys are ignored, and an
entry without an `id` is dropped rather than failing the whole list.

```json
[ { "id": "66f0…", "name": "Hof", "state": "CONNECTED" } ]
```

### `GET /v1/cameras/{id}/rtsps-stream`

The RTSPS URLs currently enabled for one camera, keyed by quality:

```json
{ "high": "rtsps://10.0.0.1:7441/TOKEN?enableSrtp", "low": null }
```

An empty object means RTSP is off for every quality, which is Protect's
default for a new camera.

The quality keys are **not** a fixed set. `high`, `medium`, `low` and
`package` (the doorbell's second lens) are the common ones, but dual-sensor
and 180° cameras add their own, so the plugin keeps the mapping dynamic rather
than modelling fixed fields.

### `POST /v1/cameras/{id}/rtsps-stream`

Enables RTSP for the given qualities and returns the same shape as the GET.

```json
{ "qualities": ["high"] }
```

This is what spares the user from walking through the Protect UI camera by
camera, switching RTSP on and copying tokens. It writes to the console, so the
plugin only issues it on an explicit user action — the setup screen can ask
for the current state without it (`enable=False` in `Controller.stream_url`).

### `GET /v1/cameras/{id}/snapshot?highQuality=false`

Raw JPEG bytes. `highQuality=true` asks for 1080p or better. The snapshot
overlay leaves it off: the picture is scaled into a corner anyway, and a
larger image costs both console time and decode time on the receiver.

This endpoint returns HTTP 503 when the console cannot reach the camera --
observed against a console with two cameras in `DISCONNECTED` state, both
answering 503 in about 40 ms. It is also reported under load. Either way the
client maps it to `ProtectUnavailableError`, a retryable condition rather
than a configuration problem; the channel list turns it into an OFFLINE card
rather than a retry, having already asked `/v1/cameras` whether the camera is
up.

## Stream URLs

Protect issues RTSPS URLs of the form:

```
rtsps://<console>:7441/<token>?enableSrtp
```

The token *is* the credential: anyone holding it can watch that camera. The
plugin never writes one to the log — `logger.redact_url()` exists for exactly
this — and never puts one in a bouquet file either. What enigma2 is handed is
a loopback URL carrying a token of the proxy's own.

Those proxy tokens come in two kinds. The corner's are invented per session,
so one that leaks into a log or a forum post stops working at the next
restart. The bouquet's are kept in the plugin's camera file and survive a
restart, because a reference sitting in a channel list has to work tomorrow.
That is acceptable only because the proxy binds to `127.0.0.1`: the URL is
useless to anyone who is not already on the receiver, and it names no camera
and no Protect token.

### The plain-RTSP fallback

Some firmware also serves unencrypted RTSP on 7447, same token, no query
string. `models.to_plain_rtsp()` performs that rewrite, exposed in setup as
*Use unencrypted RTSP*. It exists because some ffmpeg and GStreamer builds on
set-top boxes cannot complete a TLS handshake against a self-signed
certificate.

It is off by default for two reasons: the port does not exist on every Protect
version, and sending camera video unencrypted across the network should be a
decision, not a default. A console reached through a port forward on some
other port keeps that port, since the matching plain-RTSP forward cannot be
guessed.

## Codecs

Protect's **Standard** encoding is H.264; **Enhanced** is H.265. H.265 over
the PiP decoder fails on many receivers, which surfaces as enigma2's
"Incorrect type service for PiP!" rather than as anything codec-shaped. The
low and medium tiers are H.264 on most camera models and are the safer choice
on older hardware.
