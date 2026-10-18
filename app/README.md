# Camera Access App

A sample iOS application demonstrating integration with Meta Wearables Device Access Toolkit. This app walks the SDK's camera lifecycle as explicit steps — start a session, start the preview, capture or record, stop the preview, end the session — in a single full-screen camera view.

## Features

- Connect to Meta Ray-Ban Display glasses
- Explicit camera lifecycle: start/end a device session and start/stop the live preview
- Stream the camera feed from the device
- Show the same detection cards on the glasses and over the phone's live camera preview
- Capture photos
- Record video, with optional sound-in-video captured from the glasses' microphone
- Backgrounding the app ends the active preview session and returns the sample to a clean idle state
- Preview and share captured photos and recorded videos
- Open the firmware update flow when required

## Prerequisites

- iOS 17.2+
- Xcode 26.4+
- Swift 6.3+
- Meta Wearables Device Access Toolkit (included as a dependency)
- Meta Ray-Ban Display glasses for hardware testing (optional for development)

## Building the app

- Before running on a device, select your Apple development team and use your own bundle identifier. Developer Mode needs no credentials; for other release channels, set `META_APP_ID` and `CLIENT_TOKEN` using credentials from Wearables Developer Center.

### Using Xcode

1. Clone this repository
1. Open the project in Xcode
1. Select your target device
1. Click the "Build" button or press `Cmd+B` to build the project
1. To run the app, click the "Run" button (▶️) or press `Cmd+R`

## Running the app

1. Turn 'Developer Mode' on in the Meta AI app.
1. Launch the app.
1. Press the "Connect" button to complete app registration.
1. Tap "Start Session" to connect to your glasses, then "Preview" to begin the live camera feed.
1. Use the on-screen controls to:
   - Capture photos
   - Record video, toggling the glasses' microphone for sound-in-video
   - Preview and share captured photos and recorded videos
   - Stop the preview, end the session, or disconnect from the device
1. If the app backgrounds while previewing or recording, CameraAccess ends the active session; when you return, start again from "Start Session".
1. If a firmware update is required, tap "Update firmware".
1. If session start reports that the app on the glasses is outdated, tap "Update app on glasses".

## Troubleshooting

### Mock video playback

In a Debug build, open the ladybug menu, enable MockDeviceKit, pair glasses and
turn on Power, Donned and Unfolded. Choose **Camera source → Video file** and
select a movie from Photos. Wait for **Preparing video…** to disappear, dismiss
the menu, then start a session and the preview. Local playback works with the
Iris server URL left blank.

Select your original 1080p HEVC movie directly from Photos; CameraAccess prepares
it automatically before loading the mock feed. Conversion runs in the background
with a progress bar and Cancel button. The result is an HEVC (`hvc1`) MP4 at 24 fps,
up to 540×960 portrait or 960×540 landscape, preserving the source aspect ratio
and orientation. HDR originals are rendered to SDR. The mock feed uses video only.

The converter uses an explicit HEVC Main encoder with frame reordering disabled,
a one-second keyframe interval and a target bitrate of 1.2 Mb/s. It does not use
an export preset that can add B-frames. Existing HEVC clips at a suitable size and
frame rate are copied unchanged only after checking frame sizes and timing.
Every compressed frame is checked before the file reaches the mock SDK: its
packet counters can trap on large HEVC keyframes. Oversized frames are retried
at a smaller resolution and bitrate. Failed or cancelled imports leave the
previous camera feed selected.

For a sample with no B-frames, tap **Load sample video (720p)** in the mock device
card, then start the session and preview. This loads the bundled
`TestResources/iris_nobframes_720p.mp4` directly into `setCameraFeed(fileURL:)`,
bypassing the picker and transcoder. The portrait HEVC clip is 720×1280 at 24 fps,
derived from `plant.mp4` with libx265, `bframes=0`, a closed 24-frame GOP and a
1600 kb/s VBV limit. Use this sample when checking smooth mock playback without
waiting for a conversion.

The phone currently shows directional detection indicators from `POST /frame`.
That response contains no box coordinates. Set the server URL and enable
**Send frames and speak alerts** to receive detections; camera playback alone
doesn't run an on-device detector.

The live preview fits each frame to the available screen while preserving its
aspect ratio, with black margins when needed. Directional indicators appear at
the top of the screen below the camera controls. The preview and detection uploads
use the same complete frame, including for landscape, square and tall videos.

### Iris uploads and detection HUD

Tap the waveform button on the camera screen to open **Iris status**. Enter the
server's base URL (without `/frame`) and its key, if one is required, then enable
**Send frames and speak alerts**. The URL and key are saved locally; uploads start
off each time the app launches. Start the session and live preview using either
glasses or the Mock Device Kit to send frames.

For a development server, use a Debug build and enter
`http://<server-ip>:8000` (with your server's actual port). Debug builds allow HTTP
to numeric IPv4 and IPv6 addresses through ATS range exceptions; no personal IP
is embedded in the project. The address you enter stays in the app's local
settings. Rebuild and reinstall after changing the ATS configuration. Release
builds omit these HTTP exceptions; use your HTTPS server URL for Release.

The Info.plist is preprocessed by Xcode: `IRIS_DEBUG_HTTP` is defined only in the
app target's Debug configuration. The CIDR entries describe address ranges and
do not identify a particular server.

The app sends a fresh 640-pixel-wide JPEG at most every 400 ms, with one request at
a time and a 1.5-second timeout. Resizing and JPEG encoding at 0.7 quality run in a
detached background task after the preview image is published. Errors appear in
Iris status and uploads retry on subsequent frames. Pausing or stopping the
preview cancels pending uploads and speech. The HUD shows server detections under
Left, Ahead and Right; the API does
not supply bounding boxes. Each side's indicator stays visible through brief
misses, dims to 50% while missing, and fades out after three misses or 1.2 seconds
since its last detection. Failed requests do not count as misses; the overlay
also clears after two seconds without a successful frame reply. Stopping the
preview or changing upload settings clears it immediately.
Alerts and Gemini scene descriptions appear in status as replies arrive. Only
Gemini descriptions are spoken, once per new optional `description_id`, using
the existing Apple text-to-speech voice. Server alerts remain display-only.
Descriptions wait for current speech; only the newest waiting description is
kept, and it is dropped if it has waited more than five seconds. Stopping the
preview, disabling uploads, or changing the server URL or key clears pending
speech and remembered description IDs.
Replies without `description_id` remain compatible and display descriptions only.

### Meta Ray-Ban Display HUD and phone preview

Start a session with Meta Ray-Ban Display glasses to connect their display
automatically. The display shares the camera's device session. Open **Iris
status**, configure the server and enable **Send frames and speak alerts**, then
start **Preview** to receive detections. The phone has one live camera view with
the detection HUD across the top. Photos, recording and uploads use the camera
frames beneath that HUD.

The app renders shared 600×600 HUD artwork and sends it through `MWDATDisplay`.
Its compact cards sit at the top, with left/ahead/right directions,
confidence percentages and dimmed held detections. Empty directions draw nothing.
Each column shows up to three detections with an overflow count; other directions
have two named summary lines beneath the cards. With no detections the display
stays unlit. The phone composites the transparent artwork over its camera feed;
the glasses receive that same artwork on a black canvas. The wearer sees the HUD over their
surroundings directly; the camera video is not sent back into the display.

**Iris status** shows the display connection, HUD delivery and detection state.
Failed transfers retry with the newest image, and **Retry display** reconnects a
failed display without ending the camera session. Display updates never overlap or queue old
frames. Detection expiry runs independently of the visible phone view, including
while Iris status is open. Pausing/stopping the camera or disabling uploads
removes detections; ending the session or backgrounding the app stops the display.

For development on an iPhone without Display glasses, enable MockDeviceKit in the
ladybug menu and choose **Pair Meta Ray-Ban Display**. Turn on Power, Donned and
Unfolded, load a camera feed, and start the session and preview. Pair a single
powered device to make the camera's automatic device selection unambiguous.
Device selection, mock pairing and test setup target Meta Ray-Ban Display only.

Physical-device verification is still required for the display's optical size,
firmware image layout, camera/display operation together and reconnection. The
phone composites the sent HUD image over camera frames; it does not read the
hardware framebuffer or reproduce Meta's system UI.

`FrameUploaderTests` covers the shared JSON fixtures, multipart/JPEG formatting,
background JPEG drawing, throttling, cancellation and recovery using an in-memory
URL loading transport, plus held indicators across misses, errors and resets.
`DetectionHoldTests` checks per-side history, dimming, expiry and future directions.
`DetectionHUDSnapshotTests` checks the shared HUD content and freshness, and
`HUDDeliveryBufferTests` checks transfer coalescing, retries and session invalidation.
`FrameAlertSpeakerTests` checks description deduplication, pending replacement,
expiry, delayed callbacks and resets without playing audio.
`MockVideoPreparerTests` checks HEVC passthrough, temporary
file ownership, 1080p conversion in both orientations, H.264 imports, B-frame
removal, cancellation and the bundled sample's frame timing.

For issues related to the Meta Wearables Device Access Toolkit, please refer to the [developer documentation](https://wearables.developer.meta.com/docs/develop/) or visit our [discussions forum](https://github.com/facebook/meta-wearables-dat-ios/discussions)

## License

This source code is licensed under the license found in the LICENSE file in the root directory of this source tree.
