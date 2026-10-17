"""Evaluate YOLO checkpoints on one shared dataset (see training/EVALUATION.md)."""

import argparse
import csv
import math
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MODELS = [
    "yolo26s.pt",
    "server/weights/best_plain.pt",
    "server/weights/best_storm.pt",
]
FIELDS = [
    "timestamp_utc", "model", "data", "split", "classes", "images",
    "precision", "recall", "map50", "map50_95", "inference_ms_per_image",
    "preprocess_ms_per_image", "postprocess_ms_per_image", "device",
    "imgsz", "batch", "conf", "iou", "ultralytics_version", "artifacts",
]


def ordered_names(names):
    if isinstance(names, list) and names:
        result = names
    elif isinstance(names, dict) and names:
        indexed = {int(key): value for key, value in names.items()}
        if sorted(indexed) != list(range(len(indexed))):
            raise ValueError("Class IDs must be contiguous and start at zero.")
        result = [indexed[i] for i in range(len(indexed))]
    else:
        raise ValueError("data.yaml must contain a nonempty names list or mapping.")
    if not all(isinstance(name, str) for name in result):
        raise ValueError("Class names must be strings.")
    return result



def audit_dataset(meta, data, split):
    """Require explicit, valid ground truth before calculating accuracy."""
    root = Path(meta.get("path", data.parent))
    if not root.is_absolute():
        root = data.parent / root
    entry = meta[split]
    entries = entry if isinstance(entry, list) else [entry]
    images = []
    for entry in entries:
        folder = Path(entry)
        if not folder.is_absolute():
            folder = root / folder
        if not folder.is_dir():
            raise ValueError(f"Split must point to an image directory: {folder}")
        images.extend(p.resolve() for p in folder.rglob("*")
                      if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"})
    if not images or len(set(images)) != len(images):
        raise ValueError("Split is empty or repeats image paths.")
    names = ordered_names(meta["names"])
    positives = boxes = 0
    for image in images:
        parts = list(image.parts)
        if "images" not in parts:
            raise ValueError(f"Expected YOLO images/labels layout: {image}")
        index = len(parts) - 1 - parts[::-1].index("images")
        parts[index] = "labels"
        label = Path(*parts).with_suffix(".txt")
        if not label.is_file():
            raise ValueError(f"Missing annotation (use explicit empty files for backgrounds): {label}")
        rows = [line.split() for line in label.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        for row in rows:
            if len(row) != 5:
                raise ValueError(f"Expected five-value detection annotations: {label}")
            cls = int(row[0])
            coords = [float(value) for value in row[1:]]
            if not 0 <= cls < len(names) or not all(math.isfinite(v) and 0 <= v <= 1 for v in coords) or min(coords[2:]) <= 0:
                raise ValueError(f"Invalid class ID or bounding box: {label}")
        positives += bool(rows)
        boxes += len(rows)
    if not boxes:
        raise ValueError("No annotated objects: cannot calculate meaningful AP.")
    return {"images": len(images), "positive_images": positives,
            "background_images": len(images) - positives, "boxes": boxes}


def evaluate(args):
    import yaml

    data = args.data.resolve()
    with data.open(encoding="utf-8-sig") as handle:
        meta = yaml.safe_load(handle)
    if not isinstance(meta, dict):
        raise ValueError("Dataset YAML must contain a mapping.")
    if not meta.get(args.split):
        raise ValueError(
            f"{data} has no {args.split!r} split. Ask C for an independent test "
            "split, or use --split val for preliminary validation only."
        )
    names = ordered_names(meta.get("names"))
    audit = audit_dataset(meta, data, args.split)
    print(f"Dataset audit: {audit}")
    if args.check_data:
        return
    import ultralytics
    from ultralytics import YOLO

    # Ultralytics compares numerical class IDs; matching names alone is insufficient.
    # This permits stock COCO person=0 on a one-class person=0 dataset.
    class_ids = list(range(len(names)))
    if args.output.exists():
        with args.output.open(newline="", encoding="utf-8") as handle:
            if next(csv.reader(handle), None) != FIELDS:
                raise ValueError(f"Existing CSV has a different schema: {args.output}")
    for checkpoint in args.models:
        if checkpoint not in {"yolo26s.pt"} and not Path(checkpoint).is_file():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    for index, checkpoint in enumerate(args.models):
        model = YOLO(checkpoint)
        if model.task != "detect":
            raise ValueError(f"{checkpoint} is not a bounding-box detection model.")
        model_names = ordered_names(model.names)
        if model_names[:len(names)] != names:
            raise ValueError(
                f"Class IDs differ for {checkpoint}: dataset={names}, "
                f"model={model_names}. Remap classes before comparing."
            )
        validation = {}

        def capture_validation(validator):
            validation["artifacts"] = str(validator.save_dir)
            validation["images"] = validator.seen

        model.add_callback("on_val_end", capture_validation)
        metrics = model.val(
            data=str(data), split=args.split, classes=class_ids,
            imgsz=args.imgsz, batch=args.batch, device=args.device,
            conf=args.conf, iou=args.iou, workers=0, rect=False,
            half=False, augment=False, seed=0, plots=True,
            project=str(args.project), name=f"{index}_{Path(checkpoint).stem}",
            exist_ok=False,
        )
        if validation["images"] != audit["images"]:
            raise ValueError("Validation skipped images; inspect corrupt-image warnings before reporting results.")
        row = dict(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            model=checkpoint, data=str(data), split=args.split,
            classes=";".join(names), images=audit["images"],
            precision=float(metrics.box.mp), recall=float(metrics.box.mr),
            map50=float(metrics.box.map50), map50_95=float(metrics.box.map),
            inference_ms_per_image=metrics.speed["inference"],
            preprocess_ms_per_image=metrics.speed["preprocess"],
            postprocess_ms_per_image=metrics.speed["postprocess"],
            device=str(model.device), imgsz=args.imgsz,
            batch=args.batch, conf=args.conf, iou=args.iou,
            ultralytics_version=ultralytics.__version__,
            artifacts=validation["artifacts"],
        )
        new_file = not args.output.exists()
        with args.output.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
        print(f"Saved {checkpoint} results to {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/test/data/data.yaml"))
    parser.add_argument("--models", nargs="+", default=[DEFAULT_MODELS[0]])
    parser.add_argument("--check-data", action="store_true", help="Audit annotations without loading models")
    parser.add_argument("--split", choices=["test", "val"], default="test")
    parser.add_argument("--device", default="cpu", help="cpu, 0 for CUDA, or mps")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--conf", type=float, default=0.001)
    parser.add_argument("--iou", type=float, default=0.7, help="NMS IoU, not AP matching IoU")
    parser.add_argument("--output", type=Path, default=Path("docs/results.csv"))
    parser.add_argument("--project", type=Path, default=Path("runs/evaluation"))
    args = parser.parse_args()
    if args.imgsz <= 0 or args.batch <= 0:
        parser.error("--imgsz and --batch must be positive")
    if not 0 <= args.conf <= 1 or not 0 <= args.iou <= 1:
        parser.error("--conf and --iou must be between zero and one")
    try:
        evaluate(args)
    except (ValueError, FileNotFoundError, ImportError) as exc:
        parser.exit(1, f"Evaluation failed: {exc}\n")


if __name__ == "__main__":
    main()
