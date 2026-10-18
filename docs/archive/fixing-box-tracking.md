# Full-Screen Box Tracking Protocol

## Problem

The server currently reduces each detection to a horizontal direction (`left`, `right`, `ahead`). The app only gets a lateral signal, so the box can slide along the x axis but has no idea where the person is vertically. The box can never follow someone up or down the screen.

## Goal

The server sends the full position of every detected person on every frame. The app draws each box exactly where the person is, anywhere on screen, in both x and y.

## Core Rule

Send coordinates, not directions. Directions like `left` / `right` / `ahead` (and `up` / `down`) can be derived on the app side from coordinates if the UI still needs them. They should never be the only thing on the wire.

---

## 1. Coordinate System

- All box values are **normalized to the source frame**, range `0.0` to `1.0`.
- Origin `(0, 0)` is the **top-left** of the frame.
- `x` increases to the right, `y` increases **downward** (standard image convention).
- Normalizing means the server and app never have to agree on pixel resolution. The app scales to whatever its screen size is.

```
(0,0) ───────────────► x (1.0)
  │
  │      ┌──────┐
  │      │ box  │
  │      └──────┘
  ▼
  y (1.0)
```

## 2. Message Format

One message per processed frame. Send it even when there are zero detections so the app can clear stale boxes.

```json
{
  "type": "detections",
  "frame_id": 1042,
  "timestamp_ms": 1760745600123,
  "frame_width": 1280,
  "frame_height": 720,
  "detections": [
    {
      "track_id": 3,
      "label": "person",
      "confidence": 0.87,
      "box": { "x": 0.41, "y": 0.22, "w": 0.12, "h": 0.46 },
      "center": { "x": 0.47, "y": 0.45 }
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `frame_id` | Increasing counter. App drops any message older than the last one it drew. |
| `timestamp_ms` | When the frame was captured. Used for latency checks and smoothing. |
| `frame_width`, `frame_height` | Source resolution. Needed for aspect ratio, not for scaling. |
| `track_id` | Same person keeps the same id across frames. Lets the app move one box instead of redrawing a new one. |
| `box.x`, `box.y` | Top-left corner, normalized. |
| `box.w`, `box.h` | Width and height, normalized. |
| `center` | Convenience field. Equal to `x + w/2`, `y + h/2`. |
| `confidence` | Model score. App can hide boxes below a threshold. |

## 3. Server Side

### What changes

Wherever the server currently does something like:

```python
if cx < 0.33:
    direction = "left"
elif cx > 0.66:
    direction = "right"
else:
    direction = "ahead"
```

replace it with sending the box itself. Most detection models (YOLO, MediaPipe, etc.) already output both x and y. The y values are being computed and then thrown away.

### Building the message

```python
def build_message(frame_id, timestamp_ms, frame_w, frame_h, results):
    detections = []
    for r in results:
        x1, y1, x2, y2 = r.x1, r.y1, r.x2, r.y2   # pixel coords from the model

        x = x1 / frame_w
        y = y1 / frame_h
        w = (x2 - x1) / frame_w
        h = (y2 - y1) / frame_h

        detection = {
            "track_id": r.track_id,
            "label": "person",
            "confidence": round(r.confidence, 3),
            "box": {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)},
            "center": {"x": round(x + w / 2, 4), "y": round(y + h / 2, 4)},
        }
        detections.append(detection)

    message = {
        "type": "detections",
        "frame_id": frame_id,
        "timestamp_ms": timestamp_ms,
        "frame_width": frame_w,
        "frame_height": frame_h,
        "detections": detections,
    }
    return message
```

Notes:
- If the model already returns normalized values, skip the division but double check it is not normalized against a resized input (e.g. 640x640) instead of the original frame. That mismatch will make y look squashed or offset.
- If the model returns center format `(cx, cy, w, h)`, convert to top-left: `x = cx - w/2`, `y = cy - h/2`.
- Clamp all values to `[0, 1]` before sending.

### Tracking IDs

If the pipeline has no tracker yet, a simple one is enough for a demo: match each new box to the previous frame's box with the highest overlap (IoU). If IoU is above about `0.3`, reuse that `track_id`, otherwise assign a new one. ByteTrack or the built-in tracker in Ultralytics also work if already installed.

## 4. App Side

### Mapping to screen

The camera frame and the display usually do not have the same aspect ratio. Figure out how the frame is shown (stretched, letterboxed, or cropped to fill) and map with the same rule.

**Letterboxed (frame fits inside screen, bars on the sides or top/bottom):**

```
scale    = min(screen_w / frame_w, screen_h / frame_h)
shown_w  = frame_w * scale
shown_h  = frame_h * scale
offset_x = (screen_w - shown_w) / 2
offset_y = (screen_h - shown_h) / 2

left   = offset_x + box.x * shown_w
top    = offset_y + box.y * shown_h
width  = box.w * shown_w
height = box.h * shown_h
```

**Cropped to fill (frame covers the screen, edges cut off):**

Same formulas, but use `max(...)` instead of `min(...)` for `scale`. The offsets come out negative, which is correct.

**Stretched:**

```
left   = box.x * screen_w
top    = box.y * screen_h
width  = box.w * screen_w
height = box.h * screen_h
```

If the box tracks well horizontally but drifts vertically after this change, the display mode is almost always the cause.

### Updating boxes

On each message:

1. If `frame_id` is less than or equal to the last drawn `frame_id`, ignore it.
2. For every detection, look up the box with the same `track_id`. Move it if it exists, create it if not.
3. Remove any box whose `track_id` was not in this message (or has been missing for more than ~3 frames, to avoid flicker).

### Smoothing (optional)

Raw boxes jitter. A simple blend per coordinate fixes most of it:

```
alpha = 0.5
shown.x = shown.x + alpha * (new.x - shown.x)
shown.y = shown.y + alpha * (new.y - shown.y)
shown.w = shown.w + alpha * (new.w - shown.w)
shown.h = shown.h + alpha * (new.h - shown.h)
```

Higher `alpha` follows faster, lower is steadier. Apply it to y as well as x.

### Keeping direction hints

If the UI still shows "left / right / ahead" text for first responders, compute it on the app from `center`:

```
horizontal = "left" if center.x < 0.33 else "right" if center.x > 0.66 else "ahead"
vertical   = "up"   if center.y < 0.33 else "down"  if center.y > 0.66 else "level"
```

## 5. Transport

- WebSocket (or whatever the current channel is) with one JSON message per frame.
- Target 10 to 15 messages per second. Higher is fine if the link keeps up.
- If messages back up, the server should drop old frames rather than queue them. The app only cares about the newest one.

## 6. Testing Checklist

- [ ] Log one raw server message and confirm `box.y` and `box.h` are present and change as a person moves up and down.
- [ ] Hard-code a fake detection at `{x: 0.0, y: 0.0, w: 0.2, h: 0.2}` and confirm the box appears in the top-left corner of the app.
- [ ] Repeat at `{x: 0.8, y: 0.8, w: 0.2, h: 0.2}` and confirm bottom-right.
- [ ] Walk across the frame diagonally and confirm the box follows on both axes.
- [ ] Crouch or move toward the top/bottom edge and confirm the box does not get stuck at a fixed height.
- [ ] Rotate between portrait and landscape (if supported) and confirm the mapping still lines up.
- [ ] Leave the frame and confirm the box disappears within a few frames.

## 7. Migration Steps

1. Add `box` and `center` to the server message while still sending the old `direction` field, so nothing breaks.
2. Update the app to draw from `box`.
3. Run through the testing checklist.
4. Remove `direction` from the server once the app no longer reads it.