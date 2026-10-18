# Iris app: frame uploader and spoken alerts

Spec for Person A's iOS app. It covers the part of the app that sends glasses camera frames to the Iris server and speaks the alerts it returns. The server is already running and tested. This file is everything the app needs to talk to it.

Server contract details also live in `docs/server-design.md` (section 3) and `docs/contract/*.json`.

## Goal

While the glasses or the Mock Device Kit are streaming, the app sends about 2–3 frames per second to the server. Whenever a reply contains an `alert`, the app speaks it out loud, for example "possible person, left".

## Server URL

- Make the URL an editable setting in the app. Never hard-code it.
- During development: `http://10.136.152.212:8000`. That is Person B's laptop on the venue wifi; ask B if it changes.
- For the demo: an `https://...nip.io` URL on Vultr, which B will send later.
- Endpoint: `POST <server>/frame`

## Request

| Item | Value |
| --- | --- |
| Method | `POST` |
| Path | `/frame` |
| Body | `multipart/form-data` with exactly one field named `file` |
| File content | One JPEG image of the current frame |
| Content-Type of the part | `image/jpeg` |
| Image size | Resize to **640 px wide**, keeping the aspect ratio |
| JPEG quality | **0.7** (about 50 KB per frame) |
| Size limit | Bodies over **2 MB** are rejected with `413` |
| Header (later) | `X-Iris-Key: <key>`. B sends the key before the Vultr deploy. Add a settings field for it now and send the header whenever the field isn't empty. The server ignores it until B turns the key on. |

Example with curl, which is exactly what the app must reproduce:

```bash
curl -F "file=@frame.jpg;type=image/jpeg" http://10.136.152.212:8000/frame
```

## Sending rules

1. **About 2–3 frames per second.** Take the latest frame every ~400 ms.
2. **One request at a time.** If the previous request hasn't returned yet, skip this frame. Never queue frames.
3. **One shared `URLSession`** for every upload, created once and reused, not one per request. That keeps the connection open, which matters a lot on cellular.
4. **Timeout of 1.5 s** per request. On a timeout, drop the request and send the next frame.
5. **Never stop streaming because of an error.** Any failure (timeout, non-200 status, decode error) means "no alert for this frame". Log it and carry on.
6. **On/off toggle.** Uploading only runs while the user has it switched on.

## Reply

Status `200` with JSON:

```json
{
  "detections": [
    {"label": "person", "conf": 0.71, "side": "left"}
  ],
  "alert": "possible person, left",
  "frame_id": 1842,
  "server_ms": 38
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `detections` | array | Can be empty. Each item has `label` (String), `conf` (Double, 0..1) and `side` (String: `left`, `ahead` or `right`). |
| `alert` | String or null | Text to speak. `null` means say nothing. The server decides when to alert, so the app never needs its own thresholds or cooldowns. |
| `frame_id` | Int, optional | Increases with every frame. Useful for logs. |
| `server_ms` | Int, optional | Server processing time in ms. Round-trip time minus this is network time. |
| `description` | String, optional | Added later: a one-sentence scene description. Show it on the status screen; don't speak it. |

### Decoding rules (important)

- Decode `label` and `side` as **`String`**, not as a strict Swift `enum`. The model may add new labels such as `limb`, and a strict enum would fail to decode the whole reply, which would silently stop every alert.
- Make `frame_id`, `server_ms` and `description` **optional** (`Int?`, `String?`).
- Ignore unknown keys. Swift's `Codable` does this by default; don't turn it off.
- If decoding fails, treat it as "no alert" and keep streaming.

Example fixtures to test decoding against, in the repo:
- `docs/contract/frame-alert.json`: a reply with an alert
- `docs/contract/frame-quiet.json`: no detections, `alert` is null
- `docs/contract/frame-extended.json`: optional fields, a `limb` label and an unknown `future_field`, all of which must decode without errors

## Error replies

| Status | Meaning | What the app does |
| --- | --- | --- |
| 400 | Body wasn't a valid image | Log it and continue. Check the JPEG encoding if it repeats. |
| 401 | Wrong or missing `X-Iris-Key` (only once B enables the key) | Show "check server key" on the status screen and continue. |
| 413 | Body over 2 MB | Frames aren't being resized; fix the resize step. |
| 503 | Server still loading the model | Continue; it clears within seconds. |
| Timeout or network error | Server unreachable | Show it on the status screen and continue. |

Error bodies look like `{"detail": "..."}`.

## Speaking alerts

- Speak the `alert` string with `AVSpeechSynthesizer`, through one shared instance.
- If something is already being spoken, stop it (`stopSpeaking(at: .immediate)`) and speak the new alert.
- The glasses are a Bluetooth audio device, so speech plays through them automatically. With the Mock Device Kit it plays from the phone speaker.
- Don't speak `description` or anything else.

## Info.plist

Plain HTTP to the laptop's IP needs:

- `NSAppTransportSecurity` → `NSAllowsLocalNetworking` = `YES`
- `NSLocalNetworkUsageDescription` = e.g. "Sends camera frames to the Iris server on your local network."

The Vultr URL uses HTTPS, so it needs nothing extra.

## Status screen

Show:
- the server URL (editable) and the key field
- the on/off toggle
- the last alert text and when it arrived
- the last error, if any
- optionally the last round-trip time and `server_ms`

## Acceptance checks

1. With the server running, toggling uploads on shows `POST /frame` lines in B's server log at about 2–3 per second, all returning `200`.
2. Pointing the camera at a person makes the app say "possible person, ..." within about 1–2 seconds. It doesn't repeat every frame; the server spaces the alerts out.
3. Pointing the camera at an empty scene produces no speech.
4. Stopping the server shows an error on the status screen, the app keeps running, and uploads recover by themselves when the server comes back.
5. All three `docs/contract/*.json` fixtures decode without errors.
6. No upload is ever larger than 2 MB, and none gets a 413.

## Out of scope for this task

- Switching automatically to a fallback server URL. Planned later: after 3 failures in a row, switch to a second URL.
- The `X-Iris-Key` value itself. B sends it before the Vultr deploy.
- The Gemini `description` field. Display it once the server starts sending it.
