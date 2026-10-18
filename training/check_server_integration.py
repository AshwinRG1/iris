"""Check a running Iris server's checkpoint identity and real frame/live-view contract.

Run against a dedicated test server: uploading frames can create detection events.
API keys are read only from IRIS_API_KEY. This does not test iOS or speech.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlsplit


def verify_health(health, expected_file, expected_sha):
    if health.get("ready") is not True or health.get("load_error") is not None:
        raise ValueError("Server detector is not ready.")
    if health.get("fallback") is not None:
        raise ValueError("Server fell back to another detector.")
    if (
        health.get("model_file") != expected_file
        or health.get("model_sha256") != expected_sha
    ):
        raise ValueError("Server checkpoint does not match the requested weights.")
    if "person" not in (health.get("class_names") or []):
        raise ValueError("Server does not report a person class.")


def number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def verify_frame(reply, previous_id):
    frame_id = reply.get("frame_id")
    if (
        not isinstance(frame_id, int)
        or isinstance(frame_id, bool)
        or frame_id <= previous_id
    ):
        raise ValueError("Frame IDs must advance.")
    if not number(reply.get("server_ms")) or reply["server_ms"] < 0:
        raise ValueError("Invalid server latency.")
    if not isinstance(reply.get("detections"), list):
        raise ValueError("Missing detections list.")
    if reply.get("alert") is not None and not isinstance(reply["alert"], str):
        raise ValueError("Invalid alert.")
    if reply.get("description") is not None and not isinstance(
        reply["description"], str
    ):
        raise ValueError("Invalid description.")
    if reply.get("description_id") is not None and not isinstance(
        reply["description_id"], int
    ):
        raise ValueError("Invalid description ID.")
    for detection in reply["detections"]:
        if (
            detection.get("label") != "person"
            or not number(detection.get("conf"))
            or not 0 <= detection["conf"] <= 1
        ):
            raise ValueError("Invalid person label/confidence.")
        box = detection.get("box", {})
        values = [box.get(k) for k in ("x", "y", "w", "h")]
        if not all(number(v) and 0 <= v <= 1 for v in values):
            raise ValueError("Missing or invalid normalized box.")
        x, y, w, h = values
        if w <= 0 or h <= 0 or x + w > 1.000001 or y + h > 1.000001:
            raise ValueError("Box extends outside the image.")
        center = detection.get("center", {})
        if (
            not all(number(center.get(k)) for k in ("x", "y"))
            or abs(center["x"] - x - w / 2) > 1e-5
            or abs(center["y"] - y - h / 2) > 1e-5
        ):
            raise ValueError("Center does not match the box.")
        expected_side = (
            "left"
            if center["x"] < 1 / 3
            else "right"
            if center["x"] > 2 / 3
            else "ahead"
        )
        if detection.get("side") != expected_side:
            raise ValueError("Direction does not match the box.")
    return frame_id


def run(url, model, images, output, repeat=3, timeout=1.5):
    import httpx
    from PIL import Image, ImageOps

    if output.exists():
        raise FileExistsError(output)
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "Use a server base URL without credentials, query, or fragment."
        )
    key = os.environ.get("IRIS_API_KEY", "").strip()
    if not key.isascii() or (key and not key.isprintable()):
        raise ValueError("IRIS_API_KEY contains invalid characters.")
    if (
        key
        and parts.scheme != "https"
        and parts.hostname not in ("localhost", "127.0.0.1", "::1")
    ):
        raise ValueError("Use HTTPS when sending an API key to a remote server.")
    expected_sha = hashlib.sha256(model.read_bytes()).hexdigest()
    headers = {"X-Iris-Key": key} if key else {}
    samples = []
    for image_path in images:
        with Image.open(image_path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image = image.resize((640, max(1, round(image.height * 640 / image.width))))
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=70)
            samples.append((image_path.name, buf.getvalue()))
    rows = []
    previous_id = -1
    with httpx.Client(
        base_url=url.rstrip("/") + "/",
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        response = client.get("health")
        response.raise_for_status()
        health = response.json()
        verify_health(health, model.name, expected_sha)
        live = client.get("latest")
        live.raise_for_status()
        initial_live = live.json()
        if not isinstance(initial_live.get("tuning"), dict):
            raise ValueError("Live view does not expose active tuning.")
        for name, jpeg in samples:
            for _ in range(repeat):
                started = time.perf_counter()
                response = client.post(
                    "frame", files={"file": ("frame.jpg", jpeg, "image/jpeg")}
                )
                elapsed = (time.perf_counter() - started) * 1000
                response.raise_for_status()
                reply = response.json()
                previous_id = verify_frame(reply, previous_id)
                rows.append({"image": name, "round_trip_ms": elapsed, "reply": reply})
                time.sleep(max(0, 0.4 - (time.perf_counter() - started)))
        response = client.get("latest")
        response.raise_for_status()
        latest = response.json()
        if (
            latest.get("boot_id") != initial_live.get("boot_id")
            or latest.get("frame_id", -1) < previous_id
        ):
            raise ValueError("Server restarted or live view did not receive the frame.")
        for path in ("", "latest.jpg", "events"):
            response = client.get(path)
            response.raise_for_status()
            if path == "latest.jpg" and not response.headers.get(
                "content-type", ""
            ).startswith("image/jpeg"):
                raise ValueError("Live-view image is not JPEG.")
            if path == "events" and not isinstance(response.json().get("events"), list):
                raise ValueError("Invalid events response.")
        response = client.get("health")
        response.raise_for_status()
        verify_health(response.json(), model.name, expected_sha)
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "real_http_model_and_frame_contract",
        "server_url": url,
        "model_file": model.name,
        "model_sha256": expected_sha,
        "health": health,
        "active_tuning": initial_live["tuning"],
        "requests": len(rows),
        "frames": rows,
        "passed": True,
        "accuracy_scored": False,
        "phone_speech_tested": False,
        "limitations": [
            "No survivor ground truth or detection-rate claim.",
            "No iPhone, glasses HUD, Gemini delivery or audible speech verification.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"PASS: {len(rows)} frame requests; checkpoint identity, frame contract and live-view endpoints verified. Report: {output}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--images", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=1.5)
    args = parser.parse_args()
    if args.repeat < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("repeat and timeout must be positive")
    run(args.url, args.model, args.images, args.output, args.repeat, args.timeout)
