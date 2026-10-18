"""Score held-out saved predictions at validation-frozen thresholds; never sweep test."""

import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path

from failure_report import match, metrics
from evaluate import audit_dataset, ordered_names


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def score(data, ap_csv, selection_file, output):
    import yaml
    from PIL import Image

    if output.exists() or output.with_suffix(".json").exists():
        raise FileExistsError(output)
    selection = json.loads(selection_file.read_text())
    if selection.get("schema") != 1 or selection.get("selection_split") != "val":
        raise ValueError("Thresholds must originate from validation.")
    if not selection.get("models") or not selection.get("validation_image_sha256"):
        raise ValueError("Incomplete frozen provenance.")
    meta = yaml.safe_load(data.read_text())
    audit = audit_dataset(meta, data, "test")
    if ordered_names(meta["names"]) != ["person"]:
        raise ValueError("This scorer requires person at ID zero only.")
    root = Path(meta.get("path", data.parent))
    root = root if root.is_absolute() else data.parent / root
    entries = meta["test"] if isinstance(meta["test"], list) else [meta["test"]]
    paths = []
    for entry in entries:
        folder = Path(entry)
        folder = folder if folder.is_absolute() else root / folder
        paths.extend(
            p.resolve()
            for p in folder.rglob("*")
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        )
    if len({p.name for p in paths}) != len(paths):
        raise ValueError("Filename collision prevents safe JSON association.")
    records = []
    val = set(selection["validation_image_sha256"])
    for p in sorted(paths):
        digest = sha(p)
        if digest in val:
            raise ValueError("Test overlaps threshold-selection images.")
        parts = list(p.parts)
        index = len(parts) - 1 - parts[::-1].index("images")
        parts[index] = "labels"
        label = Path(*parts).with_suffix(".txt")
        with Image.open(p) as im:
            w, h = im.size
        gt = []
        for line in label.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            cls, x, y, bw, bh = map(float, line.split())
            box = [
                max(0, (x - bw / 2) * w),
                max(0, (y - bh / 2) * h),
                min(w, (x + bw / 2) * w),
                min(h, (y + bh / 2) * h),
            ]
            area = (box[2] - box[0]) * (box[3] - box[1]) * (640 / max(w, h)) ** 2
            gt.append(
                {
                    "box": box,
                    "size": "small"
                    if area < 32**2
                    else "medium"
                    if area < 96**2
                    else "large",
                }
            )
        records.append(
            {
                "image": str(p),
                "file_name": p.name,
                "sha256": digest,
                "label_sha256": sha(label),
                "gt": gt,
            }
        )
    with ap_csv.open(newline="", encoding="utf-8") as handle:
        ap = list(csv.DictReader(handle))
    ap_map = {str(Path(row["model"]).resolve()): row for row in ap}
    if len(ap_map) != len(ap):
        raise ValueError(
            "Duplicate model rows in AP results; use a fresh comparison CSV."
        )
    rows = []
    group_rows = {}
    all_cases = {}
    for point in selection["models"]:
        model = Path(point["model"])
        digest = sha(model)
        if digest != point["model_sha256"]:
            raise ValueError(
                "Checkpoint hash changed after threshold selection: " + str(model)
            )
        threshold = float(point["confidence"])
        if not 0.001 <= threshold <= 1:
            raise ValueError("Invalid frozen threshold")
        row = ap_map[str(model.resolve())]
        if (
            int(row["images"]) != len(records)
            or Path(row["data"]).resolve() != data.resolve()
            or row["split"] != "test"
        ):
            raise ValueError("AP run and test manifest differ.")
        provenance = json.loads(
            (Path(row["artifacts"]) / "prediction_provenance.json").read_text()
        )
        if (
            provenance["checkpoint_sha256"] != digest
            or provenance["data_yaml_sha256"] != sha(data)
            or provenance["split"] != "test"
            or provenance["images"] != len(records)
        ):
            raise ValueError(
                "Saved prediction provenance does not match frozen checkpoint/test dataset."
            )
        if (
            provenance["imgsz"] != 640
            or provenance["nms_iou"] != 0.7
            or provenance["conf"] != 0.001
            or provenance["rect"]
            or provenance["half"]
            or provenance["augment"]
        ):
            raise ValueError(
                "Saved prediction settings differ from the declared protocol."
            )
        pred_path = Path(row["artifacts"]) / "predictions.json"
        raw = json.loads(pred_path.read_text())
        predictions = defaultdict(list)
        expected = {r["file_name"] for r in records}
        for item in raw:
            if item.get("file_name") not in expected:
                raise ValueError("Prediction references an unknown image.")
            if item["category_id"] != 1:
                raise ValueError("Person-only JSON export required.")
            x, y, w, h = item["bbox"]
            predictions[item["file_name"]].append(
                [x, y, x + w, y + h, item["score"], 0]
            )
        tp = fp = fn = 0
        groups = {s: {"tp": 0, "fn": 0} for s in ["small", "medium", "large"]}
        cases = []
        for r in records:
            result = match(predictions[r["file_name"]], r["gt"], [], threshold)
            matched = {gi for _, gi in result["pairs"]}
            tp += len(result["tp"])
            fp += len(result["fp"])
            fn += len(result["fn"])
            for gi, g in enumerate(r["gt"]):
                groups[g["size"]]["tp" if gi in matched else "fn"] += 1
            cases.append(
                {
                    "file_name": r["file_name"],
                    "tp": len(result["tp"]),
                    "fp": len(result["fp"]),
                    "fn": len(result["fn"]),
                }
            )
        rows.append(
            {
                "model": str(model),
                "model_sha256": digest,
                "confidence": threshold,
                "images": len(records),
                "boxes": audit["boxes"],
                **metrics(tp, fp, fn),
            }
        )
        for stats in groups.values():
            stats["recall"] = (
                stats["tp"] / (stats["tp"] + stats["fn"])
                if stats["tp"] + stats["fn"]
                else 0
            )
        group_rows[point["name"]] = groups
        all_cases[point["name"]] = cases
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result = {
        "selection": str(selection_file.resolve()),
        "selection_sha256": sha(selection_file),
        "validation_provenance_verified": selection["validation_provenance_verified"],
        "deployment_approved": False,
        "test_data": str(data.resolve()),
        "test_data_sha256": sha(data),
        "records": records,
        "rows": rows,
        "size_groups": group_rows,
        "per_image": all_cases,
        "protocol": "IoU 0.5 greedy confidence-order one-to-one matches at frozen development threshold; exported AP predictions; not Ultralytics confidence-curve summary or alert metrics.",
        "limitation": "C actual train/val exclusions and near duplicates remain unverified. Unmatched predictions are annotation-relative. Test thresholds were not swept.",
    }
    output.with_suffix(".json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(rows, indent=2), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--ap-results", type=Path, required=True)
    p.add_argument("--selection", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    score(a.data.resolve(), a.ap_results, a.selection, a.output)
