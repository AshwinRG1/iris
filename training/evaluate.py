"""Evaluate YOLO checkpoints on one shared dataset (see training/EVALUATION.md)."""

import argparse
import csv
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


def evaluate(args):
    import yaml
    import ultralytics
    from ultralytics import YOLO

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
        metrics = model.val(
            data=str(data), split=args.split, classes=class_ids,
            imgsz=args.imgsz, batch=args.batch, device=args.device,
            conf=args.conf, iou=args.iou, workers=0, rect=False,
            half=False, augment=False, seed=0, plots=True,
            project=str(args.project), name=f"{index}_{Path(checkpoint).stem}",
            exist_ok=False,
        )
        row = dict(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            model=checkpoint, data=str(data), split=args.split,
            classes=";".join(names), images=model.validator.seen,
            precision=float(metrics.box.mp), recall=float(metrics.box.mr),
            map50=float(metrics.box.map50), map50_95=float(metrics.box.map),
            inference_ms_per_image=metrics.speed["inference"],
            preprocess_ms_per_image=metrics.speed["preprocess"],
            postprocess_ms_per_image=metrics.speed["postprocess"],
            device=str(model.validator.device), imgsz=args.imgsz,
            batch=args.batch, conf=args.conf, iou=args.iou,
            ultralytics_version=ultralytics.__version__,
            artifacts=str(metrics.save_dir),
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
    parser.add_argument("--data", type=Path, default=Path("data/merged/data.yaml"))
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
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
