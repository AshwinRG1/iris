"""
Merge multiple Roboflow YOLO-format exports into data/merged/ with a single class set.
Each source dataset must contain images/{train,val}, labels/{train,val}, and data.yaml.

Usage:
    python training/merge_datasets.py --datasets data/rubble data/flood --classes person
"""

import argparse
import shutil
import yaml
from pathlib import Path


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def remap_label_file(
    src: Path,
    dst: Path,
    src_classes: list[str],
    unified: list[str],
    name_map: dict[str, str],
    dropped: set[str],
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    unified_lower = {u.lower(): u for u in unified}
    lines = []
    for line in src.read_text().splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        old_id = int(parts[0])
        raw = src_classes[old_id]
        lower = raw.lower()
        # 1. exact match, 2. case-insensitive match, 3. explicit name_map
        if raw in unified:
            target = raw
        elif lower in unified_lower:
            target = unified_lower[lower]
        elif lower in name_map:
            target = name_map[lower]
        else:
            dropped.add(raw)
            continue
        new_id = unified.index(target)
        lines.append(f"{new_id} " + " ".join(parts[1:]))
    dst.write_text("\n".join(lines) + ("\n" if lines else ""))


def merge(dataset_dirs: list[Path], out_dir: Path, classes: list[str], name_map: dict[str, str]):
    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    all_dropped: set[str] = set()

    for ds_dir in dataset_dirs:
        meta = load_yaml(ds_dir / "data.yaml")
        src_classes: list[str] = meta.get("names", [])
        prefix = ds_dir.name
        print(f"\n{ds_dir.name} source classes: {src_classes}")

        # Support both layouts: images/train/ and train/images/
        # Also map "valid" -> "val"
        split_map = {"train": "train", "val": "val", "valid": "val"}
        for src_split, out_split in split_map.items():
            if (ds_dir / "images" / src_split).exists():
                img_src = ds_dir / "images" / src_split
                lbl_src = ds_dir / "labels" / src_split
            elif (ds_dir / src_split / "images").exists():
                img_src = ds_dir / src_split / "images"
                lbl_src = ds_dir / src_split / "labels"
            else:
                continue
            split = out_split
            for img in img_src.iterdir():
                stem = f"{prefix}_{img.stem}"
                shutil.copy2(img, out_dir / "images" / split / (stem + img.suffix))
                lbl = lbl_src / (img.stem + ".txt")
                dst_lbl = out_dir / "labels" / split / (stem + ".txt")
                if lbl.exists():
                    remap_label_file(lbl, dst_lbl, src_classes, classes, name_map, all_dropped)
                else:
                    dst_lbl.write_text("")

    if all_dropped:
        print(f"\nDropped (not mapped to any unified class): {sorted(all_dropped)}")

    data_yaml = {
        "path": str(out_dir.resolve()),
        "train": "images/train",
        "val": "images/val",
        "nc": len(classes),
        "names": classes,
    }
    with open(out_dir / "data.yaml", "w") as f:
        yaml.dump(data_yaml, f, default_flow_style=False)

    print(f"\nMerged {len(dataset_dirs)} datasets -> {out_dir}")
    print(f"Classes ({len(classes)}): {classes}")
    for split in ("train", "val"):
        imgs = len(list((out_dir / "images" / split).iterdir()))
        boxes = sum(
            len([l for l in (out_dir / "labels" / split / f.stem).with_suffix(".txt").read_text().splitlines() if l.strip()])
            for f in (out_dir / "images" / split).iterdir()
            if (out_dir / "labels" / split / f.stem).with_suffix(".txt").exists()
        )
        print(f"  {split}: {imgs} images, {boxes} boxes")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", nargs="+", required=True, help="Roboflow dataset dirs")
    parser.add_argument("--out", default="data/merged", help="Output directory")
    parser.add_argument("--classes", nargs="+", default=["person"], help="Unified class names")
    parser.add_argument(
        "--name-map", nargs="*", default=[],
        metavar="SRC:UNIFIED",
        help="Explicit name mappings e.g. 'person drowning:person' (case-insensitive)",
    )
    args = parser.parse_args()

    name_map: dict[str, str] = {}
    for mapping in (args.name_map or []):
        src, unified = mapping.split(":", 1)
        name_map[src.strip().lower()] = unified.strip()

    merge(
        dataset_dirs=[Path(d) for d in args.datasets],
        out_dir=Path(args.out),
        classes=args.classes,
        name_map=name_map,
    )


if __name__ == "__main__":
    main()
