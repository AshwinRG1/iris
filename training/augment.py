"""
Generate a storm-augmented copy of a YOLO dataset for controlled comparison.
Copies the original dataset to --out, then adds one storm copy of every training image.
Val split is copied unchanged so both datasets share the same eval images.

Usage:
    python training/augment.py --data data/merged --out data/merged_storm
"""

import argparse
import random
import shutil
import yaml
import cv2
import numpy as np
from pathlib import Path


def add_rain(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    rain = np.zeros_like(img)
    for _ in range(random.randint(500, 1500)):
        x = random.randint(0, w - 1)
        y = random.randint(0, h - 1)
        length = random.randint(10, 30)
        cv2.line(rain, (x, y), (x + random.randint(-2, 2), y + length), (200, 200, 200), 1)
    return cv2.addWeighted(img, 1.0, rain, 0.4, 0)


def add_fog(img: np.ndarray) -> np.ndarray:
    fog = np.full_like(img, 220)
    alpha = random.uniform(0.3, 0.55)
    return cv2.addWeighted(img, 1 - alpha, fog, alpha, 0)


def add_motion_blur(img: np.ndarray) -> np.ndarray:
    size = random.choice([7, 9, 11, 15])
    kernel = np.zeros((size, size))
    if random.random() < 0.5:
        kernel[size // 2, :] = 1
    else:
        kernel[:, size // 2] = 1
    kernel /= size
    return cv2.filter2D(img, -1, kernel)


def add_darkness(img: np.ndarray) -> np.ndarray:
    factor = random.uniform(0.35, 0.65)
    return np.clip(img.astype(np.float32) * factor, 0, 255).astype(np.uint8)


def add_noise(img: np.ndarray) -> np.ndarray:
    noise = np.random.normal(0, random.uniform(10, 25), img.shape).astype(np.float32)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


AUGMENTATIONS = [add_rain, add_fog, add_motion_blur, add_darkness, add_noise]


def storm_augment(img: np.ndarray) -> np.ndarray:
    for fn in random.sample(AUGMENTATIONS, k=random.randint(2, 4)):
        img = fn(img)
    return img


def copy_split(src_root: Path, dst_root: Path, split: str):
    for kind in ("images", "labels"):
        src = src_root / kind / split
        dst = dst_root / kind / split
        if src.exists():
            shutil.copytree(src, dst, dirs_exist_ok=True)


def add_storm_copies(img_dir: Path, lbl_dir: Path) -> int:
    images = list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.png"))
    count = 0
    for img_path in images:
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        aug = storm_augment(img)
        stem = img_path.stem + "_storm"
        cv2.imwrite(str(img_dir / (stem + img_path.suffix)), aug)
        src_lbl = lbl_dir / (img_path.stem + ".txt")
        dst_lbl = lbl_dir / (stem + ".txt")
        dst_lbl.write_text(src_lbl.read_text() if src_lbl.exists() else "")
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/merged", help="Original YOLO dataset root")
    parser.add_argument("--out", default="data/merged_storm", help="Output directory for storm dataset")
    args = parser.parse_args()

    src = Path(args.data)
    dst = Path(args.out)

    if dst.exists():
        shutil.rmtree(dst)

    print(f"Copying {src} -> {dst} ...")
    for split in ("train", "val"):
        copy_split(src, dst, split)

    # copy data.yaml and patch the path
    meta = yaml.safe_load((src / "data.yaml").read_text())
    meta["path"] = str(dst.resolve())
    with open(dst / "data.yaml", "w") as f:
        yaml.dump(meta, f, default_flow_style=False)

    print("Adding storm copies to training split ...")
    n = add_storm_copies(dst / "images" / "train", dst / "labels" / "train")
    print(f"  {n} storm copies added")
    print("Done. Val split is identical to the original for a fair comparison.")


if __name__ == "__main__":
    main()
