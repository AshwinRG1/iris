"""Replay harness: streams a video clip to a running server the way the app does.

Talks HTTP only. Samples the clip at 2.5 fps, resizes to 640 px wide, encodes JPEG at
quality 70 and posts one frame at a time (multipart field `file`), in real time: the frame
for clip second t goes out t seconds after the start. A late reply makes the harness skip
the slots it missed rather than burst.

Needs OpenCV (`pip install -r requirements-ml.txt`). Run from server/; --url is required:

    python -m scripts.replay clip.mp4 --url http://localhost:8001
    python -m scripts.replay clip.mp4 --url URL --expect sightings.json
    python -m scripts.replay clip.mp4 --url URL --expect sightings.json --preflight
        [--commit abc123]

API key: if the server sets IRIS_API_KEY, export the same variable before running and every
POST /frame carries it as X-Iris-Key (/health is unkeyed). There is deliberately no flag for
it, so the key stays out of shell history and `ps`. The key is never printed. A 401 prints one
line saying the key is missing or wrong, and the run goes on.

    export IRIS_API_KEY=...   # same value as on the server

Sightings file: a JSON list of {"start": seconds, "end": seconds}, in clip time.
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from store import nearest_rank

SAMPLE_FPS = 2.5
FRAME_WIDTH = 640
JPEG_QUALITY = 70
START_WINDOW_S = 2.0  # a sighting needs an alert within this long of its start
GRACE_S = 1.0  # an alert may trail the end of a sighting by this long
EPS = 1e-6  # float slack when comparing clip times
KEY_ENV = "IRIS_API_KEY"
KEY_HEADER = "X-Iris-Key"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


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
        if not any(s.start - EPS <= t <= s.start + START_WINDOW_S + EPS for t in alert_times)
    ]
    problems += [
        f"alert at {t:g}: outside every sighting plus {GRACE_S:g} s grace"
        for t in alert_times
        if not any(s.start - EPS <= t <= s.end + GRACE_S + EPS for s in sightings)
    ]
    return problems


def auth_headers(env: Mapping[str, str]) -> dict[str, str]:
    """Headers for POST /frame: the stripped key from IRIS_API_KEY, or none when blank.

    Raises ValueError (message never contains the key) for non-ASCII or control characters.
    """
    key = env.get(KEY_ENV, "").strip()
    if not key.isascii() or not key.isprintable():
        raise ValueError(f"{KEY_ENV} has invalid characters")
    return {KEY_HEADER: key} if key else {}


def is_cleartext_remote(url: str, headers: Mapping[str, str]) -> bool:
    """True when a key would travel over plain http to a non-local host."""
    parsed = urlparse(url)
    return bool(headers) and parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS


def check_health(health: dict[str, Any], commit_prefix: str | None) -> list[str]:
    problems = []
    if not health.get("ready"):
        problems.append(f"not ready (load_error: {health.get('load_error')})")
    if not health.get("detector"):
        problems.append("no detector reported")
    if commit_prefix and not str(health.get("commit") or "").startswith(commit_prefix):
        problems.append(f"commit {health.get('commit')!r} does not start with {commit_prefix!r}")
    return problems


# Shell: video and HTTP ----------------------------------------------------------------------


def sample_frames(clip: Path) -> Iterator[tuple[float, bytes]]:
    """Yield (clip seconds, JPEG bytes) at SAMPLE_FPS, by each frame's own timestamp."""
    import cv2  # only the harness needs it; CI doesn't install it

    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        raise SystemExit(f"cannot open clip: {clip}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    index = 0  # frames read
    k = 0  # samples taken; sample k is due at k / SAMPLE_FPS
    try:
        while True:
            ok, image = cap.read()
            if not ok:
                return
            pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            t = pos_ms / 1000 if pos_ms > 0 or index == 0 else index / src_fps
            index += 1
            if t < k / SAMPLE_FPS - EPS:
                continue
            k += 1
            height = round(image.shape[0] * FRAME_WIDTH / image.shape[1])
            small = cv2.resize(image, (FRAME_WIDTH, height), interpolation=cv2.INTER_AREA)
            ok, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                yield t, jpeg.tobytes()
    finally:
        cap.release()


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f}"


def replay(client: httpx.Client, clip: Path, headers: Mapping[str, str]) -> tuple[list[Alert], int]:
    """Play the clip. Returns the alerts and how many /frame replies were 401."""
    alerts: list[Alert] = []
    server_ms: list[float] = []
    rtt_ms: list[float] = []
    sent_at: list[float] = []
    errors = skipped = auth_errors = 0
    slot_s = 1 / SAMPLE_FPS
    start = time.perf_counter()
    for clip_s, jpeg in sample_frames(clip):
        due = start + clip_s
        now = time.perf_counter()
        if now > due + slot_s:  # a late reply used up this slot: skip, don't burst
            skipped += 1
            continue
        if now < due:
            time.sleep(due - now)
        sent = time.perf_counter()
        sent_at.append(sent)
        try:
            r = client.post(
                "/frame", files={"file": ("frame.jpg", jpeg, "image/jpeg")}, headers=headers
            )
            r.raise_for_status()
            rtt_ms.append((time.perf_counter() - sent) * 1000)  # 2xx only
            body = r.json()
            server_ms.append(body["server_ms"])
            alert = body["alert"]
        except httpx.HTTPStatusError as e:
            # Fixed text only: an error's own message could carry request details.
            errors += 1
            if e.response.status_code == 401:
                auth_errors += 1
                if auth_errors == 1:
                    print(f"401 from /frame: {KEY_ENV} is missing or wrong (server wants a key)")
            else:
                print(f"t={clip_s:6.2f}s  ERROR  HTTP {e.response.status_code} from /frame")
            continue
        except httpx.LocalProtocolError:
            errors += 1
            print(f"t={clip_s:6.2f}s  ERROR  refused to send the request (bad header value?)")
            continue
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            errors += 1
            print(f"t={clip_s:6.2f}s  ERROR  {type(e).__name__}: {e}")
            continue
        if alert:
            alerts.append(Alert(clip_s, alert))
            print(f"t={clip_s:6.2f}s  ALERT  {alert}")
    fps = (len(sent_at) - 1) / (sent_at[-1] - sent_at[0]) if len(sent_at) > 1 else 0.0
    s50, s95 = nearest_rank(server_ms, 0.5), nearest_rank(server_ms, 0.95)
    r50, r95 = nearest_rank(rtt_ms, 0.5), nearest_rank(rtt_ms, 0.95)
    print(
        f"frames={len(sent_at)} errors={errors} auth_errors={auth_errors} skipped={skipped}  "
        f"fps={fps:.2f}  "
        f"server_ms p50={_fmt(s50)} p95={_fmt(s95)}  "
        f"round_trip_ms p50={_fmt(r50)} p95={_fmt(r95)}"
    )
    return alerts, auth_errors


def preflight_health(client: httpx.Client, commit_prefix: str | None) -> bool:
    try:
        r = client.get("/health")
        r.raise_for_status()
        health = r.json()
        if not isinstance(health, dict):
            raise ValueError("/health did not return a JSON object")
    except (httpx.HTTPError, ValueError) as e:
        print(f"health FAIL: {type(e).__name__}: {e}")
        return False
    problems = check_health(health, commit_prefix)
    print(
        f"health {'FAIL' if problems else 'PASS'}: model={health.get('detector')} "
        f"commit={health.get('commit') or 'n/a'} device={health.get('device')}"
    )
    for p in problems:
        print(f"  {p}")
    return not problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("clip", type=Path)
    ap.add_argument("--url", required=True, help="server base URL, e.g. http://localhost:8001")
    ap.add_argument("--expect", type=Path, help="sightings JSON for the clip")
    ap.add_argument("--preflight", action="store_true", help="check /health, then play the clip")
    ap.add_argument("--commit", help="with --preflight: required prefix of /health's commit")
    args = ap.parse_args(argv)
    if args.preflight and not args.expect:
        ap.error("--preflight needs --expect")
    if args.commit and not args.preflight:
        ap.error("--commit only applies with --preflight")

    sightings = parse_sightings(args.expect.read_text()) if args.expect else None
    try:
        headers = auth_headers(os.environ)
    except ValueError as e:
        print(e)
        return 2
    if is_cleartext_remote(args.url, headers):
        print(
            f"warning: {KEY_ENV} is set but {args.url} is plain http; the key travels in cleartext"
        )
    with httpx.Client(base_url=args.url, timeout=30) as client:
        if args.preflight and not preflight_health(client, args.commit):
            return 1
        alerts, auth_errors = replay(client, args.clip, headers)
    problems = []
    if auth_errors:
        problems.append(f"{auth_errors} frame(s) got 401: {KEY_ENV} is missing or wrong")
    if sightings is not None:
        problems += check_sightings([a.clip_s for a in alerts], sightings)
    elif not problems:
        return 0
    print("FAIL" if problems else "PASS")
    for p in problems:
        print(f"  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
