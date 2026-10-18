"""
Fine-tune YOLO26 on the merged dataset. Runs two experiments for controlled comparison:
  - plain: trains on data/merged (original images only)
  - storm: trains on data/merged_storm (original + storm-augmented images)
Both use the same val split so metrics are directly comparable.
Weights are saved to server/weights/.

Usage:
    # both runs (recommended — run augment.py first)
    python training/train.py

    # single run
    python training/train.py --run plain
    python training/train.py --run storm

    # custom dataset paths
    python training/train.py --plain-data data/merged/data.yaml --storm-data data/merged_storm/data.yaml
"""

import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO


EPOCHS = 50
IMGSZ = 640
PATIENCE = 10
BASE_MODEL = "yolo11s.pt"
WEIGHTS_DIR = Path("server/weights")


def train_run(data: str, run_name: str, epochs: int = EPOCHS) -> Path:
    model = YOLO(BASE_MODEL)
    model.train(
        data=data,
        epochs=epochs,
        imgsz=IMGSZ,
        patience=PATIENCE,
        name=run_name,
    )
    best = Path(model.trainer.save_dir) / "weights" / "best.pt"
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    dst = WEIGHTS_DIR / f"best_{run_name}.pt"
    shutil.copy2(best, dst)
    print(f"Saved {run_name} weights -> {dst}")
    return dst


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", choices=["plain", "storm", "both"], default="both")
    parser.add_argument("--plain-data", default="data/merged/data.yaml")
    parser.add_argument("--storm-data", default="data/merged_storm/data.yaml")
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    args = parser.parse_args()

    if args.run in ("plain", "both"):
        train_run(args.plain_data, "plain", args.epochs)

    if args.run in ("storm", "both"):
        train_run(args.storm_data, "storm", args.epochs)


if __name__ == "__main__":
    main()
