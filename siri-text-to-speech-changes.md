# Siri text-to-speech changes: spoken Gemini descriptions

Implemented from [ticket 08](ticket-08-for-ryan.md) on branch `gemini-voice`.

The app now reads Gemini's environment descriptions aloud using the existing
Apple text-to-speech voice through `AVSpeechSynthesizer`. Gemini supplies the
description text, and the app handles speech ordering and playback.

## User-visible behavior

Previously, the app spoke short server alerts, such as “Possible person, left.”
Scene descriptions appeared on the status screen.

Now, an alert can be followed by a spoken description, such as “Two people to
your left.” The description also appears under **Iris status → Scene description**.

- Alerts speak immediately and interrupt any current speech.
- Descriptions wait until current speech finishes and never interrupt an alert.
- Only the newest waiting description is kept.
- A waiting description is dropped if it has waited more than five seconds.
- Each description ID is accepted once per streaming/settings session, preventing
  repeated server replies from repeating the same description.

## Server response contract

`FrameResponse` now decodes the optional field `description_id` as `Int?`, using
the Swift property `descriptionID` and the coding key `"description_id"`.

Example `/frame` response:

```json
{
  "detections": [{"label": "person", "conf": 0.92, "side": "left"}],
  "alert": "Possible person, left.",
  "description": "Two people to your left.",
  "description_id": 7,
  "frame_id": 1042,
  "server_ms": 68
}
```

The app submits the alert to the speaker first, then submits the description.
Both a description string and a non-null description ID are required for
description speech. Replies with a missing or null ID still decode successfully
and can display the description. Existing detection, alert, frame ID and server
timing fields keep their behavior.

IDs are expected to increase, as specified in the ticket. Repeated or older IDs
are ignored. Identical text with a newer ID can be spoken again.

## Speech implementation

Speech handling was moved from `FrameUploader.swift` into the new
`FrameAlertSpeaker.swift` file.

- `FrameAlertSpeaking` now includes `speakDescription(_:id:)` alongside the
  existing alert and stop methods.
- `FrameAlertSpeaker` implements `AVSpeechSynthesizerDelegate` and tracks the
  current utterance immediately when speech is requested, including before a
  speech-start callback arrives.
- Finish and cancellation callbacks release the current utterance and attempt
  to speak the newest waiting description. Callbacks for interrupted or stopped
  utterances are ignored.
- Delegate callbacks return to the main actor before updating speech state.
- Empty or whitespace-only text is ignored. Blank descriptions do not consume
  their IDs.
- Accepted IDs remain remembered when a description is replaced, interrupted or
  expires, so repeated replies cannot revive it or extend its waiting deadline.
- The five-second expiry follows the recommendation in ticket 08. Exactly five
  seconds is allowed; a longer wait causes the description to be dropped.
- `stop()` stops speech, clears the waiting description and resets remembered
  IDs. The uploader invokes it when streaming changes, uploads are toggled, or
  the server URL or key changes.

The implementation keeps one synthesizer and uses the existing audio route.
The recording audio-session configuration was not changed.

## Files changed

| File | Change |
| --- | --- |
| [FrameUploader.swift](app/CameraAccess/CameraAccess/Media/FrameUploader.swift) | Decode optional description IDs and submit descriptions after alerts. |
| [FrameAlertSpeaker.swift](app/CameraAccess/CameraAccess/Media/FrameAlertSpeaker.swift) | New speech implementation with alert priority, description deduplication, waiting, expiry and resets. Includes an injectable synthesizer boundary for tests. |
| [project.pbxproj](app/CameraAccess/CameraAccess.xcodeproj/project.pbxproj) | Register the new speech source file in the app target. |
| [FrameAlertSpeakerTests.swift](app/CameraAccess/CameraAccessTests/FrameAlertSpeakerTests.swift) | New tests for speech behavior using a synthesizer spy without playing audio. |
| [FrameUploaderTests.swift](app/CameraAccess/CameraAccessTests/FrameUploaderTests.swift) | Add coverage for optional IDs, alert-before-description submission, display-only legacy replies and speech reset calls. |
| [app/README.md](app/README.md) | Document spoken descriptions, priority, expiry, compatibility and test coverage. |

Existing camera and detection-overlay edits were present before the speech work
and are separate from the changes documented here.

## Validation completed

- All 10 speech tests passed directly on macOS using a temporary Swift package
  containing copies of the speech source and its test file. Coverage includes
  repeated IDs, newer IDs with identical text, alert priority, replacement of
  waiting descriptions, delayed callbacks, blank text, cancellation and resets.
  The expiry test checks waits of 4.999, 5.0 and 5.001 seconds.
- The changed app code type-checked in Swift 6 mode against the iPhone SDK,
  targeting iOS 17.2.
- Both the uploader and speech test files type-checked against the iPhone SDK.
  The uploader tests were not executed on a device.
- The Xcode project passed `plutil -lint`, and `git diff --check` passed.

No Xcode simulations were run. These checks do not establish audible playback
or live Gemini response timing on an iPhone.

## Server dependency and remaining device checks

The deployed server must return `description` and `description_id` for Gemini
descriptions to be spoken. Server-side Gemini generation was not implemented as
part of this app change. The local [FrameReply](server/frame_routes.py) schema
currently contains detections, alerts and timing fields; it does not yet provide
the two description fields. The deployed server's description support has not
been verified.

With the updated server and an iPhone build, verify:

1. A short alert is followed by an audible description, aiming for the ticket's
   approximately three-second timing target.
2. A stationary scene is not described repeatedly.
3. A new alert interrupts a description, while descriptions wait behind alerts.
4. Stopping the stream or changing connection settings clears waiting speech.
5. Speech reaches the intended phone or glasses output while recording is active.
6. Multiple people receive the server's combined scene description, and the
   office test video produces a description consistent with the office scene.

The reported device audio warnings remain unconfirmed; no audio-session fix was
made based solely on that console excerpt.
