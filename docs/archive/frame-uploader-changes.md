# Frame uploader and detection HUD changes

Date: October 17, 2026

Implemented the iOS frame uploader, detection overlay, and status/settings screen using `app-frame-uploader-spec.md` and the existing `/frame` contract fixtures.

## New files

### [FrameUploader.swift](app/CameraAccess/CameraAccess/Media/FrameUploader.swift)

- Accepts decoded `UIImage` video frames from the app's existing camera decoder, shared by glasses and the Mock Device Kit.
- Admits a fresh frame at most every 400 ms, approximately 2.5 frames per second. Drops frames while encoding or uploading; keeps one request in flight without queuing frames.
- Resizes images to 640 pixels wide, preserves their aspect ratio, normalizes image orientation, and encodes JPEG at quality `0.7`. Uses rendering scale `1` to avoid Retina displays multiplying the uploaded dimensions.
- Builds `POST <server>/frame` requests with exactly one multipart field named `file`, filename `frame.jpg`, and part content type `image/jpeg`.
- Rejects multipart bodies larger than `2,000,000` bytes before networking, including the multipart envelope.
- Creates one reusable `URLSession` with 1.5-second request and resource timeouts. Sends `X-Iris-Key` when the configured key is nonempty.
- Saves the editable server URL and key in `UserDefaults`. Uploads start disabled; no server address is hard-coded.
- Cancels pending work when settings, the upload toggle, or streaming state change. Prevents canceled responses from updating the UI or speaking an alert, and keeps the upload slot occupied until cancellation finishes.
- Logs HTTP, network, image-preparation, and decoding failures, then continues accepting subsequent frames. Includes a specific “Check server key” message for HTTP 401.
- Exposes observable state for detections, scene description, upload activity, last error, last alert and receipt time, round-trip time, frame ID, and server processing time.
- Adds `FrameDetection` and `FrameResponse` models. Labels and sides remain strings; `frame_id`, `server_ms`, and `description` are optional; unknown JSON fields are ignored.
- Adds an injectable speech interface and a shared `AVSpeechSynthesizer` implementation. New alerts interrupt existing speech. Only server `alert` text is spoken; descriptions remain visual. Alert thresholds and cooldowns stay on the server.
- Preserves the last alert as history across quiet or failed frames, while clearing current detections after failures and cancellation.

### [DetectionOverlayView.swift](app/CameraAccess/CameraAccess/Views/DetectionOverlayView.swift)

- Adds a transparent SwiftUI HUD over the live preview, showing detections in Left, Ahead, and Right columns with labels and confidence percentages.
- Displays future, unknown directions as text without assigning them a position.
- Uses the server's directional information; the `/frame` response does not provide bounding boxes.
- Hides indicators when uploads or streaming stop, and expires indicators after approximately two seconds without a successful reply.
- Allows touches to pass through to camera controls and adds an accessibility identifier.

### [FrameUploadStatusView.swift](app/CameraAccess/CameraAccess/Views/FrameUploadStatusView.swift)

- Adds editable server URL and secure key fields, plus the upload/speech toggle.
- Displays upload status, errors, round-trip and server timings, frame ID, last alert with its receipt time, and optional scene descriptions.
- Includes a Done button, dark appearance, and accessibility identifiers for settings and errors.

### [FrameUploaderTests.swift](app/CameraAccess/CameraAccessTests/FrameUploaderTests.swift)

Adds a serialized Swift Testing suite covering:

- All three existing JSON contract fixtures and future labels/directions.
- Multipart formatting, endpoint, timeout, optional authentication, and JPEG dimensions/aspect ratio from a Retina source.
- Busy-frame dropping, throttling, cancellation, and rapid toggle changes.
- Recovery from HTTP errors, timeouts, invalid JSON, invalid settings, and invalid images.
- Quiet responses, alert history, descriptions remaining silent, and repeated server alerts without a client cooldown.
- Dropping an oversized JPEG before networking and successfully sending the next valid frame.

Tests use an injected clock, speech spy, isolated preferences, and a controlled `URLProtocol` transport. After the first run, the transport's cancellation counter was corrected to count only pending held requests: `URLSession` can also call `stopLoading()` after successful responses. The test wait deadline was increased to ten seconds.

## Existing files updated

| File | Changes |
| --- | --- |
| [CameraViewModel.swift](app/CameraAccess/CameraAccess/ViewModels/CameraViewModel.swift) | Owns one uploader; feeds decoded live frames into it; synchronizes uploading with session and stream transitions; stops uploads on backgrounding. Live frames continue reaching the uploader while a captured photo is displayed. |
| [CameraView.swift](app/CameraAccess/CameraAccess/Views/CameraView.swift) | Places the HUD over `LivePreviewView` and adds a waveform button that opens the Iris status sheet. |
| [Info.plist](app/CameraAccess/CameraAccess/Info.plist) | Enables `NSAppTransportSecurity → NSAllowsLocalNetworking` and expands the local-network permission description to mention sending frames to Iris. |
| [project.pbxproj](app/CameraAccess/CameraAccess.xcodeproj/project.pbxproj) | Registers the three new application source files and adds `docs/contract` as a unit-test resource folder. The existing synchronized test folder discovers the new test source. |
| [app/README.md](app/README.md) | Documents configuration, upload behavior, speech, directional HUD, and test coverage. |

## Verification results and remaining checks

- **Full simulator app build passed:** Xcode reported `BUILD SUCCEEDED` for the app's generic iOS Simulator destination.
- The three new application source files also passed direct Swift type checking.
- `Info.plist` and the Xcode project passed `plutil -lint`; whitespace checks passed with `git diff --check`.
- The normal app-hosted test attempt crashed before tests ran: `Wearables.configure()` returned `WearablesError(rawValue: 0)`, followed by the SDK's fatal “Call configure() before attempting to access Wearables” error.
- A temporary test host under `/private/tmp/iris-uploader-test-host` bypassed Wearables startup and ran the actual uploader suite. **Seven of eight tests passed**, including all three decoding fixtures, multipart/JPEG formatting, throttling, recovery, and the oversized-image check. The remaining test failed on the transport cancellation counter described above.
- **The counter fix is applied, but its rerun was interrupted before starting. The corrected suite is not yet confirmed passing.**
- Real-server uploads, glasses audio routing, and end-to-end device behavior remain unverified.

Build logs, test results, and temporary build directories were created under `/private/tmp/iris-*`. Useful records include `iris-frame-uploader-final-build.log`, `iris-frame-uploader-tests-offline.log`, and `iris-uploader-isolated-tests.log`.

## Using the feature

1. Open the camera screen and tap the waveform button to open **Iris status**.
2. Enter the server base URL without `/frame`, and its key if required.
3. Enable **Send frames and speak alerts**.
4. Start a session and the live preview using glasses or the Mock Device Kit.

The existing untracked `app-frame-uploader-spec.md` was used as input and was not edited.
