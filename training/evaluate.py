"""
Evaluate any model on C's test split and demo clips.
Writes results to docs/results.csv for comparison across runs.

Supported models:
  yolo26s.pt          stock baseline
  server/weights/best_plain.pt   plain fine-tune
  server/weights/best_storm.pt   storm fine-tune
  zeroshot            OWLv2 via server/zeroshot.py

Usage:
    # evaluate one model
    python training/evaluate.py --model yolo26s.pt --data data/merged/data.yaml --clips data/demo_clips

    # evaluate all known models in one pass
    python training/evaluate.py --all --data data/merged/data.yaml --clips data/demo_clips
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import cv2
import numpy as np

DOCS_DIR = Path("docs")
RESULTS_CSV = DOCS_DIR / "results.csv"
CSV_FIELDS = ["model", "map50", "map50_95", "clip_detection_rate", "ms_per_frame"]

ALL_MODELS = [
    "yolo26s.pt",
    "server/weights/best_plain.pt",
    "server/weights/best_storm.pt",
    "zeroshot",
]


# ---------------------------------------------------------------------------
# Model wrappers — uniform interface: predict(frame_bgr) -> list[dict]
# each dict: {"label": str, "conf": float, "side": str}
# ---------------------------------------------------------------------------

def side_from_box(x1: float, x2: float, img_w: int) -> str:
    cx = (x1 + x2) / 2 / img_w
    if cx < 0.33:
        return "left"
    if cx > 0.67:
        return "right"
    return "ahead"


class YOLOPredictor:
    def __init__(self, model_path: str):
        from ultralytics import YOLO
        self.model = YOLO(model_path)

    def predict(self, frame: np.ndarray) -> list[dict]:
        h, w = frame.shape[:2]
        results = self.model(frame, verbose=False)[0]
        out = []
        for box in results.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            label = results.names[int(box.cls)]
            out.append({"label": label, "conf": float(box.conf), "side": side_from_box(x1, x2, w)})
        return out

    def val(self, data_yaml: str) -> dict:
        metrics = self.model.val(data=data_yaml, verbose=False)
        return {
            "map50": round(float(metrics.box.map50), 4),
            "map50_95": round(float(metrics.box.map), 4),
        }


class ZeroShotPredictor:
    def __init__(self):
        sys.path.insert(0, str(Path("server").resolve()))
        from zeroshot import predict as _predict
        self._predict = _predict

    def predict(self, frame: np.ndarray) -> list[dict]:
        return self._predict(frame)

    def val(self, data_yaml: str) -> dict:
        # OWLv2 has no built-in val loop; run frame-level inference on the test split
        import yaml
        meta = yaml.safe_load(Path(data_yaml).read_text())
        root = Path(meta.get("path", "."))
        test_img_dir = root / "images" / "test"
        if not test_img_dir.exists():
            print("  No test split found for zero-shot mAP — skipping")
            return {"map50": None, "map50_95": None}
        # rough detection rate as proxy (no ground-truth matching)
        images = list(test_img_dir.glob("*.jpg")) + list(test_img_dir.glob("*.png"))
        detected = sum(1 for p in images if self.predict(cv2.imread(str(p))))
        rate = round(detected / len(images), 4) if images else 0.0
        print(f"  Zero-shot test detection rate (proxy for mAP): {rate}")
        return {"map50": None, "map50_95": None}


def make_predictor(model_name: str):
    if model_name == "zeroshot":
        return ZeroShotPredictor()
    return YOLOPredictor(model_name)


# ---------------------------------------------------------------------------
# Clip evaluation
# ---------------------------------------------------------------------------

def eval_clips(predictor, clips_dir: Path) -> dict:
    clips = list(clips_dir.glob("*.mp4")) + list(clips_dir.glob("*.mov"))
    if not clips:
        print(f"  No clips found in {clips_dir}")
        return {"clip_detection_rate": None, "ms_per_frame": None}

    total_frames = 0
    detected_frames = 0
    total_ms = 0.0

    for clip in clips:
        cap = cv2.VideoCapture(str(clip))
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame = cv2.resize(frame, (640, int(frame.shape[0] * 640 / frame.shape[1])))
            t0 = time.perf_counter()
            dets = predictor.predict(frame)
            total_ms += (time.perf_counter() - t0) * 1000
            total_frames += 1
            if dets:
                detected_frames += 1
        cap.release()

    if total_frames == 0:
        return {"clip_detection_rate": None, "ms_per_frame": None}

    return {
        "clip_detection_rate": round(detected_frames / total_frames, 4),
        "ms_per_frame": round(total_ms / total_frames, 2),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def evaluate_model(model_name: str, data_yaml: str, clips_dir: Path) -> dict:
    print(f"\nEvaluating: {model_name}")
    predictor = make_predictor(model_name)

    print("  Running val on test split ...")
    val_metrics = predictor.val(data_yaml)

    print("  Running on demo clips ...")
    clip_metrics = eval_clips(predictor, clips_dir)

    row = {"model": model_name, **val_metrics, **clip_metrics}
    print(f"  {row}")
    return row


def write_csv(rows: list[dict]):
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    existing = []
    if RESULTS_CSV.exists():
        with open(RESULTS_CSV) as f:
            existing = list(csv.DictReader(f))

    # overwrite rows for models we just evaluated
    evaluated_models = {r["model"] for r in rows}
    kept = [r for r in existing if r["model"] not in evaluated_models]
    final = kept + rows

    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(final)

    print(f"\nResults written to {RESULTS_CSV}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="Model path or 'zeroshot'")
    parser.add_argument("--all", action="store_true", help="Evaluate all known models")
    parser.add_argument("--data", default="data/merged/data.yaml", help="data.yaml with test split")
    parser.add_argument("--clips", default="data/demo_clips", help="Directory of demo clips")
    args = parser.parse_args()

    if not args.model and not args.all:
        parser.error("Provide --model or --all")

    models = ALL_MODELS if args.all else [args.model]
    clips_dir = Path(args.clips)
    rows = []

    for m in models:
        if not Path(m).exists() and m != "zeroshot":
            print(f"Skipping {m} (not found)")
            continue
        rows.append(evaluate_model(m, args.data, clips_dir))

    write_csv(rows)


if __name__ == "__main__":
    main()
