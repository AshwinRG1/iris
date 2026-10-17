# Iris app: stop the detection overlay from flickering

**Owner:** A (app). **Server changes:** none. **Contract changes:** none.
**Size:** small, about an hour including tests.

## The problem

In the preview-video test, detections are correct when they show ("Ahead, Person 90%", "Right, Person 94%"), but the cards flash on and off while the person stays still. On one reply the card shows, on the next it is gone, then it comes back.

## Why it happens (current code on `main`)

The overlay has no memory between replies. Two places clear it:

1. **Every successful reply replaces the list.** In `FrameUploader.submit`, `detections = reply.detections`. One reply with no detection (motion blur, confidence dipping for a single frame) empties the overlay right away.
2. **Every failure clears it too.** `recordFailure` sets `detections = []` and `lastResponseAt = nil`, so a single timed-out or dropped request also hides the cards, and `hasFreshDetections` returns false.

At about 2.5 replies a second, one miss in every few replies is enough to look janky. The server's spoken alerts don't have this problem because they already require 2 of the last 3 frames to agree.

## The fix: hold and fade

A person counts as **gone only after about a second of misses**, not after one empty reply.

### Rules

Track display state **per side** (`left`, `ahead`, `right`, plus any unknown side), since `/frame` gives directions, not boxes or IDs.

| Event | What happens to a side's card |
| --- | --- |
| Successful reply **with** a detection on that side | Show or update the card (label, confidence). Reset its miss count to 0. Full opacity. |
| Successful reply **without** a detection on that side | Keep the card. Add 1 to its miss count. Dim it (about 50% opacity). |
| Miss count reaches **3**, or **1.2 s** since it was last seen | Remove the card with a short fade-out. |
| Request **fails or times out** | **Change nothing.** A failure is not a miss, because the server never answered. |
| No successful reply for **more than 2 s** | Clear all cards (the stream is stale). |
| Streaming stops, uploads are disabled, or the URL or key changes | Clear all cards immediately (same as today's `invalidateCurrentFrame`). |

Put the three numbers (3 misses, 1.2 s hold, 2 s stale) in named constants at the top so they're easy to tune.

### Where to change things

- **New small type, e.g. `DetectionHold`**, a plain struct with no SwiftUI, so it can be unit tested. It holds, per side, the latest detections, the last-seen time and the miss count, with methods like `apply(reply:at:)` and `expire(at:)`.
- **`FrameUploader`**: keep `detections` exactly as it is (raw latest reply). Add a `private(set) var held: DetectionHold` that is updated on each successful reply. Do **not** touch it in `recordFailure`. Reset it in `invalidateCurrentFrame`.
- **`DetectionOverlayView`**: read from the held state instead of `uploader.detections`, and gate on the 2 s stale rule instead of `hasFreshDetections`. Call `expire(at:)` from the existing `TimelineView` tick. Add `.opacity(...)` for dimmed cards, `.transition(.opacity)` for appear and disappear, and `.animation(.easeInOut(duration: 0.2), value: ...)` so changes ease in instead of jumping.

### Leave alone

- **Alerts and speech.** They stay driven only by the reply's `alert` field, exactly as now.
- **`detections`, `lastResponseAt` and the status screen.** They keep reporting the real latest reply and errors, so debugging still shows the truth.
- **The `/frame` request and decoding.** No contract change.

## Tests to add (`FrameUploaderTests` or a new `DetectionHoldTests`)

1. Hit, miss, hit on the same side: the card never disappears.
2. Three misses in a row: the card is removed.
3. Hit, then 1.2 s with no hits: the card is removed even if fewer than 3 replies arrived.
4. Hit, then a failed request: the card is unchanged and the miss count is unchanged.
5. Person moves from `ahead` to `right`: `right` appears right away, and `ahead` fades out on its own schedule.
6. No successful reply for over 2 s: all cards are cleared.
7. Stopping the stream clears everything immediately.

## How to check it worked

Replay the same preview video that showed the flicker. With a person steadily in view, the card should stay up the whole time, dimming briefly at most. When the person walks out of frame, the card should fade within about a second.
