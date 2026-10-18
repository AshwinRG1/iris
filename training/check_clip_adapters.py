"""Offline smoke test of real server detector adapters and alert policy on demo clips.

Uses nominal clip timestamps and 640px JPEG70 samples at 2.5fps. Not an HTTP/phone test.
"""

import argparse
from dataclasses import asdict
import html
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

CLIPS = [
    "rubble_survivor_extraction",
    "florence_rooftop_survivor",
    "harvey_night_flood_survivors",
    "aerial_harvey_distant_flood_group",
    "aerial_harvey_rooftop_survivors",
    "lakewithrain_mock",
    "rainy_night_negative",
]


def run(selection_path, output):
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    os.environ["OMP_NUM_THREADS"] = "8"
    import torch

    torch.set_num_threads(8)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    from detector import YoloDetector, file_sha256
    from domain import PolicyState, Tuning
    from alerts import decide

    if output.exists():
        raise FileExistsError(output)
    selection = json.loads(selection_path.read_text())
    for p in selection["models"]:
        if file_sha256(Path(p["model"])) != p["model_sha256"]:
            raise ValueError("Frozen model hash changed")
    output.mkdir(parents=True)
    (output / "frames").mkdir()
    rows = []
    snapshots = []
    for point in selection["models"]:
        name = point["name"]
        threshold = point["confidence"]
        adapter = YoloDetector(point["model"], 640, "cpu", 0.001, frozenset({"person"}))
        loaded = adapter.load()
        if (
            loaded.info.sha256 != point["model_sha256"]
            or loaded.info.fallback is not None
        ):
            raise ValueError("Wrong model loaded")
        tuning = Tuning(conf_alert=threshold)
        for clip_name in CLIPS:
            clip = Path("data/demo_clips") / (clip_name + ".mp4")
            cap = cv2.VideoCapture(str(clip))
            if not cap.isOpened():
                raise ValueError("Cannot open clip: " + str(clip))
            fps = cap.get(cv2.CAP_PROP_FPS)
            duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
            targets = [0, duration / 2]
            state = PolicyState()
            next_sample = 0.0
            idx = 0
            latencies = []
            person_frames = 0
            alerts = []
            count = 0
            with (output / (name + "_" + clip_name + ".jsonl")).open(
                "w", encoding="utf-8"
            ) as f:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    t = idx / fps
                    idx += 1
                    if t + 1e-6 < next_sample:
                        continue
                    next_sample += 0.4
                    h = round(frame.shape[0] * 640 / frame.shape[1])
                    small = cv2.resize(frame, (640, h), interpolation=cv2.INTER_AREA)
                    ok, jpeg = cv2.imencode(
                        ".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70]
                    )
                    if not ok:
                        raise ValueError("JPEG encoding failed")
                    decoded = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
                    image = Image.fromarray(cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB))
                    started = time.perf_counter()
                    detections = [
                        d
                        for d in adapter.detect(image)
                        if d.label.lower() == "person" and d.conf >= threshold
                    ]
                    elapsed = (time.perf_counter() - started) * 1000
                    latencies.append(elapsed)
                    for d in detections:
                        b = d.box
                        if (
                            not all(
                                math.isfinite(v)
                                for v in [d.conf, b.x1, b.y1, b.x2, b.y2]
                            )
                            or not 0 <= d.conf <= 1
                            or not 0 <= b.x1 <= b.x2 <= 1
                            or not 0 <= b.y1 <= b.y2 <= 1
                            or d.side not in ["left", "ahead", "right"]
                        ):
                            raise ValueError("Invalid server detection contract")
                    state, alert = decide(state, detections, round(t * 1000), tuning)
                    if alert:
                        alerts.append(
                            {
                                "clip_seconds": t,
                                "text": alert.text,
                                "side": alert.hit.side,
                            }
                        )
                    count += 1
                    person_frames += bool(detections)
                    f.write(
                        json.dumps(
                            {
                                "clip_seconds": t,
                                "detections": [asdict(d) for d in detections],
                                "alert": asdict(alert) if alert else None,
                                "detector_ms": elapsed,
                            }
                        )
                        + "\n"
                    )
                    if targets and t + 1e-6 >= targets[0]:
                        targets.pop(0)
                        draw = ImageDraw.Draw(image)
                        for d in detections:
                            b = d.box
                            draw.rectangle(
                                [b.x1 * 640, b.y1 * h, b.x2 * 640, b.y2 * h],
                                outline="#ff48e0",
                                width=2,
                            )
                            draw.text(
                                (b.x1 * 640, max(0, b.y1 * h - 12)),
                                f"person {d.conf:.2f} {d.side}",
                                fill="#ff48e0",
                            )
                        file = name + "_" + clip_name + f"_{t:.1f}.jpg"
                        image.save(output / "frames" / file, quality=90)
                        snapshots.append(
                            {
                                "file": "frames/" + file,
                                "title": f"{name}: {clip_name} at {t:.1f}s; {len(detections)} person detections",
                            }
                        )
            cap.release()
            p95 = float(np.percentile(latencies, 95))
            row = {
                "model": name,
                "clip": clip.name,
                "confidence": threshold,
                "frames": count,
                "person_frames": person_frames,
                "alerts": alerts,
                "mean_detector_ms": statistics.mean(latencies),
                "p95_detector_ms": p95,
                "frames_over_1500ms": sum(t > 1500 for t in latencies),
                "adapter_contract_passed": True,
                "expectations_scored": False,
            }
            rows.append(row)
            print(
                f"{name} {clip.name}: frames={count}, person_frames={person_frames}, alerts={len(alerts)}, p95={p95:.1f}ms",
                flush=True,
            )
    result = {
        "mode": "offline_real_server_adapter_and_alert_policy",
        "selection": str(selection_path.resolve()),
        "validation_provenance_verified": selection["validation_provenance_verified"],
        "sample_fps": 2.5,
        "upload_width": 640,
        "jpeg_quality": 70,
        "timebase": "nominal clip time, not real request arrivals",
        "rows": rows,
        "limitations": [
            "No HTTP requests, phone, network timeout or speech were tested.",
            "Inference counts/alerts are not person or survivor accuracy; target boxes and some intervals remain unfinalized.",
            "No frame-skipping/network-delay simulation; each model/clip starts with fresh alert state.",
            "Thresholds are provisional source-validation operating points, not production approval.",
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    blocks = [
        '<!doctype html><meta charset="utf-8"><title>V2 adapter clip review</title><style>body{font-family:Arial;background:#171717;color:white;max-width:1000px;margin:25px auto}img{max-width:100%}section{margin-bottom:30px}</style><h1>Offline adapter snapshots</h1><p>Predictions are not ground truth. These show real server detections at provisional thresholds; HTTP, speech, and survivor accuracy remain untested.</p>'
    ]
    blocks += [
        f'<section><h3>{html.escape(s["title"])}</h3><img loading="lazy" src="{s["file"]}"></section>'
        for s in snapshots
    ]
    (output / "gallery.html").write_text("".join(blocks), encoding="utf-8")
    print("Completed offline adapter/alert smoke tests.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.selection, args.output)
