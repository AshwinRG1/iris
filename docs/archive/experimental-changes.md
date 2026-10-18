# Experimental Branch — Changes vs Main

This file tracks everything that differs from `main` in this branch.
Do not merge to main or ashwin without team review and A confirming the iOS side works.

---

## Active Changes

### 1. Full bounding box in `/frame` response
**File:** `server/frame_routes.py`
**Commit:** `74c2f28`

Added `BoxOut` and `CenterOut` models to `DetectionOut`. Every detection now
includes normalized `box (x, y, w, h)` and `center (x, y)` alongside the
existing `side` field. `side` is preserved — nothing breaks on old clients.

**Why:** The server was computing full box coordinates from YOLO but only
sending left/ahead/right. The app had no way to draw a box at the correct
vertical position on screen.

**Status:** Server committed and pushed. Waiting on A to integrate in iOS.

---

## Integration Guide

- iOS steps: `box-tracking-ios-integration.md`
- Full protocol spec: `fixing-box-tracking.md`

---

## Pending / Not Yet Started

- [ ] Track ID — assign stable IDs per person across frames so the app moves
  one box instead of redrawing. Needs simple IoU matcher in `frame_routes.py`
  or a new `tracker.py`.
- [ ] `frame_width` / `frame_height` in `FrameReply` — lets the app know the
  source resolution for aspect ratio mapping. Low priority since coordinates
  are already normalized.

---

## How to Test

```bash
# Run server on experimental branch
cd server && uvicorn main:app --host 0.0.0.0 --port 8000

# Confirm box/center in response
curl -F "file=@test.jpg" http://localhost:8000/frame
```

Expected response shape:
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
  ],
  "alert": null,
  "frame_id": 1,
  "server_ms": 42
}
```

---

## Merge Criteria

Before merging to `main`:
- [ ] A confirms boxes render correctly on iOS at correct screen position
- [ ] A confirms boxes disappear when person leaves frame
- [ ] No regressions in existing alert logic or `/health` endpoint
- [ ] B reviews `frame_routes.py` changes
