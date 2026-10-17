# Iris Swift client implementation and server handoff

Implementation snapshot: 2026-10-17, current working tree.

This document describes the implemented iOS fixes for issues **1, 3, and 4** in
[iris-app-issues-1-3-4.md](iris-app-issues-1-3-4.md), plus the subsequent ATS fix for
configurable HTTP development servers. It gives the server developer the client
behavior, compatibility requirements, and integration checks needed to investigate
the remaining server and contract issues.

The app fixes do not change the server or the fixtures in `docs/contract/`.
The original connection review was not available in this workspace. Its remaining
issues **2, 5, 6, 7, and 8** must be mapped using that report; their descriptions
are not inferred here. Server guidance below is separate from delivered app work.
Examples use placeholders or documentation addresses, never a personal server IP.

## 1. Implementation map

| File | Responsibility |
| --- | --- |
| [FrameUploader.swift](app/CameraAccess/CameraAccess/Media/FrameUploader.swift) | URL normalization, upload admission, multipart encoding, independent upload/health sessions, response decoding, alerts, cancellation. |
| [VideoFrameDecoder.swift](app/CameraAccess/CameraAccess/Media/VideoFrameDecoder.swift) | Fresh HEVC images only; no cached-image return during decode failures or keyframe recovery. |
| [CameraViewModel.swift](app/CameraAccess/CameraAccess/ViewModels/CameraViewModel.swift) | Calls the decoder off the main actor and submits only non-nil images on the main actor. |
| [FrameUploadStatusView.swift](app/CameraAccess/CameraAccess/Views/FrameUploadStatusView.swift) | Existing settings and Check server action; health and upload errors remain separate. |
| [Info.plist](app/CameraAccess/CameraAccess/Info.plist) | Conditional Debug HTTP exceptions for numeric IP ranges, plus the existing local-network permission. |
| [project.pbxproj](app/CameraAccess/CameraAccess.xcodeproj/project.pbxproj) | Enables plist preprocessing; defines `IRIS_DEBUG_HTTP=1` only for the app's Debug configuration. |
| [FrameUploaderTests.swift](app/CameraAccess/CameraAccessTests/FrameUploaderTests.swift) | Regression coverage for URL migration/normalization, decoder recovery, separate sessions, and existing protocol behavior. |

## 2. Issue 1: normalize the server base URL

`FrameUploader.normalizedServerURL(_:)` performs these operations:

1. Trim surrounding whitespace and newlines.
2. Parse with `URLComponents`.
3. Remove trailing slashes from the path.
4. Remove **one** terminal `/frame` segment, if present, then remove any remaining
   trailing slashes.
5. Preserve the scheme, host, supplied port, and any preceding path prefix.

`endpoint(_:path:)` normalizes again before appending `frame` or `health`. It accepts
only HTTP/HTTPS URLs with a nonempty host and rejects user information, passwords,
query strings, and fragments. No port is inserted automatically.

| Entered value | Normalized base | Requests |
| --- | --- | --- |
| `http://192.0.2.10:8000` | `http://192.0.2.10:8000` | `/frame`, `/health` on port 8000 |
| `http://192.0.2.10:8000/` | Same base | Same requests |
| `http://192.0.2.10:8000/frame/` | Same base | Same requests |
| `https://iris.example.test/frame` | `https://iris.example.test` | `/frame`, `/health` over HTTPS |
| `https://iris.example.test/api/frame/` | `https://iris.example.test/api` | `/api/frame`, `/api/health` |

The last example preserves an intentional reverse-proxy prefix; the corresponding
server/proxy routes must actually exist. Other path suffixes, such as `/health`,
are not automatically removed.

Persistence uses `UserDefaults` key `iris.serverURL`. Existing saved values are
normalized and migrated on uploader initialization. Edits save the normalized
value while leaving the field's editing text intact, so typing `http://` is not
interrupted by normalization. Both request paths use the normalized value.

**Server implication:** continue serving `POST <base>/frame` and
`GET <base>/health`. The server does not need aliases for `/frame/frame` or
`/frame/health`. An omitted port means the URL's default port: HTTP 80 or HTTPS
443, rather than the development server's usual 8000.

## 3. Issue 3: skip failed decodes and recover automatically

The previous decoder could return `lastGoodImage` after a failure. The camera
listener treated that returned image as new input, so the uploader could send the
same old scene every 400 ms. The decoder no longer stores or returns that fallback.

`VideoFrameDecoder.decode(_:) -> UIImage?` now behaves as follows:

| Condition | Returned value and recovery |
| --- | --- |
| Missing compressed data or format description | `nil`. |
| New/recreated session awaiting a keyframe, sample is predicted | `nil`; continue waiting. |
| Session creation failed | `nil`; a later sample retries session creation. |
| Decode submission fails, or no successful output pixel buffer is produced | `nil`; increment consecutive failures. |
| Three consecutive decode/output failures | Invalidate and clear the session; recreate on a later sample and await a keyframe. |
| Pixel-buffer-to-image conversion fails | `nil`; no image is uploaded. |
| Successful decode and image conversion | Return a newly decoded `UIImage`. |

Successful pixel-buffer output resets the failure count. A successful keyframe
also clears `awaitingKeyframe`. Format changes and sessions that can no longer
accept the format trigger recreation and keyframe waiting. Transient failures do
not force immediate recreation: a subsequent successfully decoded frame can resume
sending before the three-failure threshold.

Checking output as well as submission status matters: VideoToolbox can return
`noErr` from submission without producing a successful output image. Such a call
must not cause reuse of an earlier image.

In `CameraViewModel.setupStreamListeners(for:)`, decoding runs off the main actor.
The existing main-actor guard submits and updates the preview only when the app
is not backgrounded and the decode returned an image:

```swift
let previewImage = self.videoFrameDecoder.decode(frame.sampleBuffer)

Task { @MainActor [weak self] in
  guard let self else { return }
  if UIApplication.shared.applicationState != .background,
     let image = previewImage {
    self.frameUploader.submit(image)
    // Existing preview/capture guards control display updates here.
  }
}
```

The displayed preview can retain its existing image during a failure, but that
displayed image is not fed back into the uploader. Recording still receives the
compressed sample independently. No restart is required for decode recovery.

**Server implications and limits:**

- Decode recovery produces a gap in incoming uploads, not repeated fallback JPEGs.
- The request contains no capture timestamp, client frame ID, heartbeat, or stale
  flag. The server cannot infer capture freshness from metadata the app does not
  send. Adding such metadata requires a separate agreement.
- An upload already admitted or in flight when decoding fails is not cancelled
  merely because of that failure. Its response can still produce an alert.
- The client does not guarantee a fixed upload cadence or an empty-scene update
  during a gap. Server detection windows and saved-frame age should account for
  elapsed time, rather than treating cached detections as new camera observations.

## 4. Issue 4: separate health and upload sessions

`FrameUploader` owns two long-lived `URLSession` instances:

```swift
self.session = session ?? Self.makeSession()
self.healthSession = healthSession ?? Self.makeSession()
```

The optional injected sessions support controlled tests. Production construction
calls the factory separately for each session. `submit(_:)` captures `session`;
`refreshServerHealth()` captures `healthSession`.

Both sessions use an ephemeral configuration with:

| Setting | Value |
| --- | --- |
| `timeoutIntervalForRequest` | 1.5 seconds |
| `timeoutIntervalForResource` | 1.5 seconds |
| `waitsForConnectivity` | `false` |
| `httpMaximumConnectionsPerHost` | `1`, independently per session |
| `urlCache` | `nil` |
| Request cache policy | `.reloadIgnoringLocalCacheData` |

Only one upload task is admitted at a time. Repeated health taps are independently
guarded by `isCheckingHealth`. A pending upload does not occupy the health
session's request slot. This removes client-session queuing; it does not guarantee
that a busy or unreachable server will respond within the timeout.

Health is public: no `X-Iris-Key` header is attached, even if an upload key is
configured or invalid. A healthy response does not authenticate that key, refresh
frame detections, speak alerts, clear an upload error, or gate subsequent uploads.
Changing the server URL cancels both kinds of work; separate generation counters
prevent cancelled results from updating current state.

**Server implication:** accept concurrent health and frame requests on separate
connections. Health should report current readiness without waiting for image
inference. Keep blocking model work off the request event loop and avoid making
health acquire the inference worker's slot.

## 5. Requests and responses the server must remain compatible with

### Frame request

| Item | Current Swift behavior |
| --- | --- |
| Method/path | `POST <normalized-base>/frame` |
| Body | Multipart form with one part named `file`, filename `frame.jpg` |
| Part content type | `image/jpeg` |
| Response preference | `Accept: application/json` |
| Image | Width 640 actual pixels, aspect ratio retained, orientation normalized, JPEG quality 0.7 |
| Image limits | Positive finite source dimensions; resized height at most 4096 pixels |
| Body limit | At most **2,000,000 bytes**, including multipart headers and closing boundary |
| Authentication | `X-Iris-Key` only when the trimmed key is nonempty |
| Key validation | After surrounding whitespace is trimmed, only printable ASCII U+0020 through U+007E is accepted |
| Cadence | Event-driven; attempts spaced at least 400 ms apart, about 2.5 fps maximum during uninterrupted operation |
| Backpressure | Frames arriving while encoding/uploading are dropped; no frame backlog |

JPEG preparation runs in a detached utility task after admission. One upload
session is reused throughout streaming. The upload slot remains occupied while
cancellation unwinds, preventing overlapping requests during rapid toggle changes.

### Frame response

HTTP **200** is decoded with `JSONDecoder` into this existing shape:

```swift
struct FrameDetection: Codable, Equatable, Sendable {
  let label: String
  let conf: Double
  let side: String
}

struct FrameResponse: Decodable, Equatable, Sendable {
  let detections: [FrameDetection]
  let alert: String?
  let frameID: Int?       // JSON: frame_id
  let serverMS: Int?      // JSON: server_ms
  let description: String?

  private enum CodingKeys: String, CodingKey {
    case detections, alert, description
    case frameID = "frame_id"
    case serverMS = "server_ms"
  }
}
```

`detections` is required and may be empty. Each detection requires all three
fields. `label` and `side` are open-ended strings; future values do not invalidate
the response. Confidence is numeric, conventionally within 0 through 1.

Optional fields accept absence or JSON null. Send integer values for `frame_id`
and `server_ms` to match their Swift types. Unknown JSON keys are ignored, but a
known field with an incompatible type fails decoding of the whole response.

Only nonblank `alert` text is spoken. It interrupts current speech. The server
owns thresholds and cooldowns: the app does not suppress repeated alerts or
require frame IDs to increase across server restarts. `description` is displayed
only. UI updates and speech run on the main actor.

### Health response

`GET <base>/health` requests JSON and expects HTTP **200**. The minimum decodable
body is:

```json
{"ready": true, "detector": "fake"}
```

| JSON field | Swift type | Required by the client |
| --- | --- | --- |
| `ready` | `Bool` | Yes |
| `detector` | `String` | Yes |
| `device`, `load_error`, `auth`, `commit`, `fallback` | `String?` | No |
| `fps` | `Double?` | No |
| `server_ms_p50`, `server_ms_p95` | `Int?` | No |

Other metadata is ignored. A non-null `load_error` displays "Model failed to
load"; otherwise `ready: false` displays "Model not ready". A ready response with
an empty detector displays "No detector reported". These snapshots never disable
frame retries. `auth` values `on` and `off` describe whether the server requires a
key; they do not prove the saved upload key is correct.

### Error and freshness behavior

Any non-200 response, timeout, image preparation error, or JSON decoding error
produces no new alert for that attempt. Current frame results are cleared after
upload failure; the last alert remains as history. Future fresh frames continue
to be admitted. Failed JPEGs are not automatically retried, and there is no
automatic server failover or offline queue.

The UI recognizes 400, 401, 413, 422, and 503 with fixed messages; other statuses
also remain recoverable. Server error bodies and raw transport descriptions are
not displayed or spoken. A 401 should mean the key needs attention; a 503 should
mean model readiness prevents processing.

On stream stop, background teardown, toggle changes, or upload-setting changes,
pending upload work is cancelled and obsolete responses are ignored. A client
timeout or cancellation does not establish that server processing stopped.

HUD detections expire two seconds after the most recent successful frame reply.
This is response-receipt age, not capture age. The app does not poll `/latest`,
`/latest.jpg`, `/events`, or `/tuning` to refresh that HUD.

## 6. ATS and private server configuration

The final implementation contains no personal server address in its ATS entries.
The server base URL is supplied through the app's settings and saved locally.

The existing `NSAllowsLocalNetworking = true` and
`NSLocalNetworkUsageDescription` remain present. The source plist additionally
contains this conditional structure:

```text
#if IRIS_DEBUG_HTTP
NSExceptionDomains:
  0.0.0.0/0:
    NSExceptionAllowsInsecureHTTPLoads: true
  ::/0:
    NSExceptionAllowsInsecureHTTPLoads: true
#endif
```

These are CIDR ranges covering numeric IPv4 and IPv6 destinations, not actual
server addresses. Apple documents IP and CIDR exceptions for iOS 17 and later in
[NSExceptionDomains](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsexceptiondomains).
The app's deployment target is iOS 17.2.

| Build setting | Debug | Release |
| --- | --- | --- |
| `INFOPLIST_PREPROCESS` | `YES` | `YES` |
| `INFOPLIST_OTHER_PREPROCESSOR_FLAGS` | `$(inherited) -traditional` | Same |
| `INFOPLIST_PREPROCESSOR_DEFINITIONS` | `$(inherited) IRIS_DEBUG_HTTP=1` | No app-defined `IRIS_DEBUG_HTTP` |
| Numeric HTTP range exceptions in processed plist | Included | Omitted |

`-traditional` preserves URL-like plist text such as `cameraaccess://` during
preprocessing. Apostrophes in permission strings are XML-escaped, preserving their
displayed content. Release's processed plist retains the original ATS policy and
metadata. No `NSAllowsArbitraryLoads` global exception was added.

After these build-setting/plist changes, rebuild and reinstall the **Debug** app
to use an HTTP numeric server address. For a hosted Release deployment, provide a
working HTTPS base URL with a valid certificate. An ATS `-1022` failure is raised
on the phone before the request reaches the server; a backend route change or
HTTP redirect cannot repair a request blocked before transport starts.

## 7. Server developer implementation guide

These are compatibility checks and implementation directions, not claims that
the remaining review issues have already been fixed.

| Area | What to verify or implement | Existing server locations to inspect |
| --- | --- | --- |
| Routing and deployment | Expose `/frame` and public `/health` under the same configured base. Forward the supplied port/path and `X-Iris-Key`. Use the intended HTTPS hostname for hosted deployment. | [frame_routes.py](server/frame_routes.py), [Caddyfile](deploy/Caddyfile) |
| Response shapes | Keep the fields and types above compatible with all three shared fixtures. Metadata must be additive. Health should remain queryable while the model is unready or failed. | [frame_routes.py](server/frame_routes.py), [contract fixtures](docs/contract/) |
| Health responsiveness | Ensure health returns during a pending inference call. The current frame route offloads decode/detect through `run_in_executor(runtime.slot, ...)`; preserve that separation when fixing server work. | [frame_routes.py](server/frame_routes.py), [main.py](server/main.py), [deps.py](server/deps.py) |
| Frame age and alert windows | Handle periods with no incoming frames without advancing alert decisions from cached images. Review elapsed-time expiry and cached snapshot age. No new capture metadata was added by this patch. | [alerts.py](server/alerts.py), [store.py](server/store.py), [live_routes.py](server/live_routes.py) |
| Limits | Keep the whole-body cap aligned at 2,000,000 bytes and the decoded-image side limit at 4096 pixels, including reverse-proxy behavior. | [limits.py](server/limits.py), [config.py](server/config.py), [frame_routes.py](server/frame_routes.py), [Caddyfile](deploy/Caddyfile) |
| Authentication and recovery | Public health must not depend on a valid upload key. Return ordinary non-200 failures for rejected frames; keep later requests serviceable. Avoid echoing credentials into errors/logs. | [auth.py](server/auth.py), [frame_routes.py](server/frame_routes.py), [server tests](server/tests/) |

The server already has implementations in several of these areas. Check the
original review and current tests before changing them. If a solution needs a new
required field, altered response type, stale marker, or client capture timestamp,
coordinate that contract change separately; this app patch does not provide it.

## 8. Acceptance checks and verification status

### Added Swift regression coverage

| Test | What it checks |
| --- | --- |
| `normalizesSavedAndEditedURLsForHealthAndUploads` | HTTP/HTTPS bases crossed with six suffix forms; persisted settings migrate, edits save the normalized base, and both endpoints are correct. |
| `failedVideoDecodesSkipUploadsAndRecoverWithoutRestarting` | Real embedded 64x64 HEVC IDR/predicted frames plus a truncated NAL; three failures and subsequent keyframe waiting create no new uploads, then keyframe/predicted sending resumes. The injected clock advances beyond the throttle interval. |
| `healthUsesItsOwnSessionDuringAnUpload` | A held upload remains pending while health completes; per-session test headers prove the requests use distinct injected sessions. |

Existing tests retain fixture decoding, future labels/directions, multipart/JPEG
formatting, authentication, throttling, cancellation, error recovery, independent
health state, and frame-ID reset coverage. The transport is a controlled
`URLProtocol`, not a live server.

### Validation completed during implementation

- All app Swift sources compiled as an iPhone device SDK module; the uploader test
  file passed device SDK type checking. This was compile/type-check validation,
  not execution of the iOS test suite.
- A native macOS harness exercised the current URL helpers and decoded all three
  contract fixtures. It also exercised the decoder with real HEVC samples and
  corrupt input; only the `UIImage` wrapper was adapted for the native harness.
- Debug and Release plists were generated through preprocessing and passed
  `plutil -lint`, as did the Xcode project. Configuration assertions checked Debug
  range exceptions, unchanged Release metadata/policy, and preserved URL schemes.
- Whitespace checks passed. **No Xcode simulations were run.**

The full iOS regression suite, physical-device integration against a live server,
and end-to-end speech/glasses behavior have not been verified by these checks.

### Joint app/server acceptance

1. On a Debug device build, set `http://<server-ip>:8000`, then repeat with `/`
   and `/frame/` appended. Confirm only the correct `/health` and `/frame` paths
   appear in server logs. Use the actual listening port if it differs.
2. Hold inference pending using a deterministic test gate. Request health through
   a second connection and verify it completes before the frame request is
   released, within the client's 1.5-second timeout budget.
3. Inject video decode failures after a good frame. Confirm no additional uploads
   contain the fallback image during failure/keyframe waiting, and sending
   resumes after successful decoding. Allow any already admitted request to finish.
4. Check public health with a missing or wrong upload key. It should still return
   readiness JSON; protected frame requests should reject the bad key. Correcting
   the key must allow later fresh uploads without restarting the app.
5. Check `ready: false`/model failure, 400/401/413/422/503 responses, invalid JSON,
   and a timeout. Confirm no new speech for failed requests and later recovery.
6. Verify all three fixtures still decode on the app side. Confirm server replies
   match their compatible structure, including optional integer metadata.
7. Verify the hosted HTTPS URL using a Release build. Confirm the built Release
   plist omits the Debug HTTP range exceptions.

For a server-only smoke check, set the base URL locally without committing it:

```sh
export IRIS_BASE_URL='http://<server-ip>:8000'
curl --max-time 1.5 "$IRIS_BASE_URL/health"
curl --max-time 1.5 -H 'Accept: application/json' \
  -F 'file=@frame.jpg;type=image/jpeg' "$IRIS_BASE_URL/frame"
```

For a keyed server, add `-H "X-Iris-Key: $IRIS_API_KEY"` to the frame command only,
with the key supplied through your local environment. These smoke commands do
not test iOS ATS, camera decoding, or concurrent health responsiveness.
