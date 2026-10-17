"""
Zero-shot person detector using OWLv2.
Designed as a drop-in fallback for B's server — predict() returns the same
shape as the YOLO wrapper in evaluate.py.

Output: list of {"label": str, "conf": float, "side": str}

Usage (standalone test):
    python server/zeroshot.py --image test.jpg
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from transformers import Owlv2ForObjectDetection, Owlv2Processor

MODEL_ID = "google/owlv2-base-patch16-ensemble"
CONF_THRESHOLD = 0.15

# Prompts tuned for partially visible people in rubble and flood scenes
PROMPTS = [
    "person",
    "human body",
    "person lying down",
    "person in rubble",
    "person in water",
    "human hand",
    "arm",
    "human face",
]

_processor: Owlv2Processor | None = None
_model: Owlv2ForObjectDetection | None = None


def _load():
    global _processor, _model
    if _model is None:
        print("Loading OWLv2 ...", file=sys.stderr)
        _processor = Owlv2Processor.from_pretrained(MODEL_ID)
        _model = Owlv2ForObjectDetection.from_pretrained(MODEL_ID)
        _model.eval()


def _side(x1: float, x2: float, img_w: int) -> str:
    cx = (x1 + x2) / 2 / img_w
    if cx < 0.33:
        return "left"
    if cx > 0.67:
        return "right"
    return "ahead"


def predict(frame: np.ndarray) -> list[dict]:
    """
    Run zero-shot detection on a BGR frame (numpy array).
    Returns detections in the same shape as the YOLO wrapper:
        [{"label": "person", "conf": 0.82, "side": "left"}, ...]
    """
    _load()
    h, w = frame.shape[:2]
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    inputs = _processor(text=[PROMPTS], images=pil_img, return_tensors="pt")

    with torch.no_grad():
        outputs = _model(**inputs)

    target_sizes = torch.tensor([[pil_img.height, pil_img.width]])
    results = _processor.post_process_grounded_object_detection(
        outputs=outputs,
        target_sizes=target_sizes,
        threshold=CONF_THRESHOLD,
    )[0]

    detections = []
    for score, box in zip(results["scores"], results["boxes"]):
        x1, y1, x2, y2 = box.tolist()
        detections.append({
            "label": "person",
            "conf": round(float(score), 4),
            "side": _side(x1, x2, w),
        })

    # deduplicate overlapping boxes (simple IoU suppression)
    return _nms(detections, results["boxes"].tolist())


def _iou(a: list, b: list) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _nms(detections: list[dict], boxes: list, iou_thresh: float = 0.5) -> list[dict]:
    if not detections:
        return []
    paired = sorted(zip(detections, boxes), key=lambda x: x[0]["conf"], reverse=True)
    kept = []
    while paired:
        best_det, best_box = paired.pop(0)
        kept.append(best_det)
        paired = [(d, b) for d, b in paired if _iou(best_box, b) < iou_thresh]
    return kept


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()

    frame = cv2.imread(args.image)
    if frame is None:
        print(f"Could not read {args.image}")
        sys.exit(1)

    dets = predict(frame)
    print(f"Detections ({len(dets)}):")
    for d in dets:
        print(f"  {d}")
