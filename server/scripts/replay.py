"""Replay harness: streams a video clip to a running server the way the app does.

Talks HTTP only. Samples the clip at 2.5 fps, resizes to 640 px wide, encodes JPEG at
quality 70 and posts one frame at a time (multipart field `file`).

    python scripts/replay.py clip.mp4 [--url http://localhost:8000] [--expect sightings.json]
    python scripts/replay.py clip.mp4 --expect sightings.json --preflight

Sightings file: a JSON list of {"start": seconds, "end": seconds}, in clip time.
"""

import argparse
import json
import math
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

SAMPLE_FPS = 2.5
FRAME_WIDTH = 640
JPEG_QUALITY = 70
START_WINDOW_S = 2.0  # a sighting needs an alert within this long of its start
GRACE_S = 1.0  # an alert may trail the end of a sighting by this long


@dataclass(frozen=True)
class Sighting:
    start: float
    end: float


@dataclass(frozen=True)
class Alert:
    clip_s: float
    text: str


# Pure logic ---------------------------------------------------------------------------------


def parse_sightings(text: str) -> list[Sighting]:
    try:
        raw = json.loads(text)
        sightings = [Sighting(float(s["start"]), float(s["end"])) for s in raw]
    except (ValueError, TypeError, KeyError) as e:
        raise ValueError(f"bad sightings file (want a list of start/end seconds): {e!r}") from e
    if any(s.end < s.start for s in sightings):
        raise ValueError("bad sightings file: a sighting ends before it starts")
    return sightings


def check_sightings(alert_times: list[float], sightings: list[Sighting]) -> list[str]:
    """Problems found, in clip seconds. Empty means PASS."""
    problems = [
        f"sighting {s.start}-{s.end}: no alert within {START_WINDOW_S:g} s of its start"
        for s in sightings
        if not any(s.start <= t <= s.start + START_WINDOW_S for t in alert_times)
    ]
    problems += [
        f"alert at {t}: outside every sighting plus {GRACE_S:g} s grace"
        for t in alert_times
        if not any(s.start <= t <= s.end + GRACE_S for s in sightings)
    ]
    return problems


def check_health(health: dict[str, Any]) -> list[str]:
    problems = []
    if not health.get("ready"):
        problems.append(f"not ready (load_error: {health.get('load_error')})")
    if not health.get("detector"):
        problems.append("no detector reported")
    return problems


def percentile(values: list[float], q: float) -> float | None:
    """Nearest rank."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(q * len(ordered)), 1) - 1]


# Shell: video and HTTP ----------------------------------------------------------------------


def sample_frames(clip: Path) -> Iterator[tuple[float, bytes]]:
    """Yield (clip seconds, JPEG bytes) at SAMPLE_FPS."""
    import cv2  # only the harness needs it; CI doesn't install it

    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        raise SystemExit(f"cannot open clip: {clip}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    index = 0
    next_at = 0.0
    try:
        while True:
            ok, image = cap.read()
            if not ok:
                return
            t = index / src_fps
            index += 1
            if t < next_at:
                continue
            next_at += 1 / SAMPLE_FPS
            height = round(image.shape[0] * FRAME_WIDTH / image.shape[1])
            small = cv2.resize(image, (FRAME_WIDTH, height), interpolation=cv2.INTER_AREA)
            ok, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                yield t, jpeg.tobytes()
    finally:
        cap.release()


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f}"


def replay(client: httpx.Client, clip: Path) -> list[Alert]:
    alerts: list[Alert] = []
    server_ms: list[float] = []
    rtt_ms: list[float] = []
    started = time.perf_counter()
    for clip_s, jpeg in sample_frames(clip):
        sent = time.perf_counter()
        r = client.post("/frame", files={"file": ("frame.jpg", jpeg, "image/jpeg")})
        rtt_ms.append((time.perf_counter() - sent) * 1000)
        r.raise_for_status()
        body = r.json()
        server_ms.append(body["server_ms"])
        if body["alert"]:
            alerts.append(Alert(round(clip_s, 2), body["alert"]))
            print(f"t={clip_s:6.2f}s  ALERT  {body['alert']}")
    elapsed = time.perf_counter() - started
    s50, s95 = percentile(server_ms, 0.5), percentile(server_ms, 0.95)
    r50, r95 = percentile(rtt_ms, 0.5), percentile(rtt_ms, 0.95)
    print(
        f"frames={len(rtt_ms)}  fps={len(rtt_ms) / elapsed:.1f}  "
        f"server_ms p50={_fmt(s50)} p95={_fmt(s95)}  "
        f"round_trip_ms p50={_fmt(r50)} p95={_fmt(r95)}"
    )
    return alerts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("clip", type=Path)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--expect", type=Path, help="sightings JSON for the clip")
    ap.add_argument("--preflight", action="store_true", help="check /health, then play the clip")
    args = ap.parse_args(argv)
    if args.preflight and not args.expect:
        ap.error("--preflight needs --expect")

    sightings = parse_sightings(args.expect.read_text()) if args.expect else None
    failed = False
    with httpx.Client(base_url=args.url, timeout=30) as client:
        if args.preflight:
            health = client.get("/health").json()
            problems = check_health(health)
            print(
                f"health {'FAIL' if problems else 'PASS'}: model={health.get('detector')} "
                f"commit={health.get('commit', 'n/a')} device={health.get('device')}"
            )
            for p in problems:
                print(f"  {p}")
            failed = bool(problems)
            if failed:
                return 1
        alerts = replay(client, args.clip)
    if sightings is not None:
        problems = check_sightings([a.clip_s for a in alerts], sightings)
        print("FAIL" if problems else "PASS")
        for p in problems:
            print(f"  {p}")
        failed = failed or bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
