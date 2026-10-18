# Box Tracking — iOS Integration Guide (Person A)

## What Changed on the Server

The `/frame` response now includes full bounding box coordinates for every detection.
`side` is still there — nothing breaks. This is additive.

**Before:**
```json
{
  "detections": [
    { "label": "person", "conf": 0.87, "side": "left" }
  ]
}
```

**After (experimental branch):**
```json
{
  "detections": [
    {
      "label": "person",
      "conf": 0.87,
      "side": "left",
      "box":    { "x": 0.41, "y": 0.22, "w": 0.12, "h": 0.46 },
      "center": { "x": 0.47, "y": 0.45 }
    }
  ]
}
```

All coordinates are normalized `0.0–1.0`. Origin `(0,0)` is top-left. `x` increases right, `y` increases down.

---

## Step 1 — Update `FrameDetection` in `DetectionHold.swift`

Add `box` and `center` as optional so the app still decodes old server responses.

```swift
struct FrameDetectionBox: Codable, Equatable, Sendable {
    let x, y, w, h: Double
}

struct FrameDetectionCenter: Codable, Equatable, Sendable {
    let x, y: Double
}

struct FrameDetection: Codable, Equatable, Sendable {
    let label: String
    let conf: Double
    let side: String
    let box: FrameDetectionBox?
    let center: FrameDetectionCenter?
}
```

---

## Step 2 — Map Normalized Coordinates to Screen in `DetectionHUDRenderer`

The camera frame and display don't share an aspect ratio. Use the letterbox formula:

```swift
let scale   = min(screenSize.width / frameWidth, screenSize.height / frameHeight)
let shownW  = frameWidth  * scale
let shownH  = frameHeight * scale
let offsetX = (screenSize.width  - shownW) / 2
let offsetY = (screenSize.height - shownH) / 2

// For each detection where box != nil:
let left   = offsetX + box.x * shownW
let top    = offsetY + box.y * shownH
let width  = box.w * shownW
let height = box.h * shownH

let rect = CGRect(x: left, y: top, width: width, height: height)
```

Use `frameWidth = 640, frameHeight = 480` (or whatever the server sends).
The server currently crops/resizes to 640px wide before inference, so use 640 as the reference width.

---

## Step 3 — Draw the Box

In `DetectionHUDRenderer`, after computing `rect`:

```swift
context.setStrokeColor(UIColor.systemYellow.cgColor)
context.setLineWidth(3)
context.stroke(rect)
```

Optionally draw a confidence label above the box:

```swift
let label = "\(detection.label) \(Int(detection.conf * 100))%"
// draw label string at CGPoint(x: rect.minX, y: rect.minY - 20)
```

---

## Step 4 — Smoothing (Optional but Recommended)

Raw boxes jitter frame to frame. Blend toward the new position:

```swift
let alpha = 0.5
shown.x += alpha * (new.x - shown.x)
shown.y += alpha * (new.y - shown.y)
shown.w += alpha * (new.w - shown.w)
shown.h += alpha * (new.h - shown.h)
```

---

## Testing Checklist

- [ ] `curl -F "file=@test.jpg" http://localhost:8000/frame` — confirm `box` and `center` appear in JSON
- [ ] Log one raw server reply and confirm `box.y` changes as a person moves up and down
- [ ] Hard-code a fake detection at `{x:0, y:0, w:0.2, h:0.2}` — box should appear top-left
- [ ] Hard-code `{x:0.8, y:0.8, w:0.2, h:0.2}` — box should appear bottom-right
- [ ] Walk diagonally across the frame — box should follow both x and y
- [ ] Leave the frame — box should disappear within 3 frames

---

## Notes

- `side` is still in the response — existing alert logic does not need to change
- `box` and `center` are new optional fields — nil on any server that hasn't pulled `experimental`
- The server branch is `experimental` — do not merge to main until A has tested and confirmed
