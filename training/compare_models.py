"""Compare saved AP predictions and render mAP / fixed-confidence model charts.

No inference or threshold search is performed. Use the predeclared comparison confidence.
The per-image reference remains an ignored run artifact; compact outputs contain no labels.
"""

import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path

from failure_report import match, metrics

ORDER = ["yolo26s", "best_plain_v2", "best_storm_v2", "best_plain_v3", "best_storm_v3"]
LABELS = [
    "Stock\nYOLO26s",
    "Plain v2\n50-epoch run",
    "Storm v2\n50-epoch run",
    "Plain v3\n15-epoch run",
    "Storm v3\n15-epoch run",
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plots(rows, directory):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    import numpy as np

    directory.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    for filename, columns, title in (
        (
            "map_comparison_v3",
            [("map50", "mAP50", "#007F86"), ("map50_95", "mAP50â€“95", "#DF9A24")],
            "YOLO person detection: mAP comparison",
        ),
        (
            "operating_point_comparison_v3",
            [
                ("fixed_precision", "Precision", "#5362BB"),
                ("fixed_recall", "Recall", "#007F86"),
                ("fixed_f2", "F2", "#DF9A24"),
            ],
            "Person detection at fixed confidence 0.15",
        ),
    ):
        fig, ax = plt.subplots(figsize=(12.5, 7))
        fig.subplots_adjust(left=0.075, right=0.98, top=0.79, bottom=0.25)
        fig.text(0.075, 0.95, title, fontsize=20, fontweight="bold", ha="left")
        fig.text(
            0.075,
            0.895,
            "Same 634 test images Â· 1,819 person boxes Â· Higher scores are better",
            fontsize=11,
            color="#444444",
        )
        xs = np.arange(len(rows))
        width = 0.7 / len(columns)
        for index, (key, label, color) in enumerate(columns):
            values = [100 * r[key] for r in rows]
            bars = ax.bar(
                xs + (index - (len(columns) - 1) / 2) * width,
                values,
                width=width * 0.92,
                label=label,
                color=color,
                zorder=3,
            )
            ax.bar_label(
                bars,
                labels=[f"{v:.2f}" for v in values],
                padding=4,
                fontsize=9,
                fontweight="bold",
            )
        ax.set_xticks(xs, LABELS)
        ax.set_ylim(0, 105)
        ax.set_yticks(range(0, 101, 20))
        ax.yaxis.set_major_formatter(PercentFormatter(100, decimals=0))
        ax.grid(axis="y", color="#E1E5E9", zorder=0)
        ax.set_axisbelow(True)
        ax.legend(
            frameon=False,
            ncol=len(columns),
            loc="lower left",
            bbox_to_anchor=(-0.01, 1.02),
        )
        fig.text(
            0.075,
            0.12,
            "CPU Â· FP32 Â· image size 640 Â· batch 1 Â· Ultralytics 8.4.178",
            fontsize=9,
            color="#555555",
        )
        fig.text(
            0.075,
            0.075,
            "Completed benchmark evaluation. Scores measure detection quality, not speed.",
            fontsize=9,
            color="#555555",
        )
        for extension in ("png", "svg", "pdf"):
            fig.savefig(
                directory / (filename + "." + extension), dpi=220, facecolor="white"
            )
        svg_path = directory / (filename + ".svg")
        svg_path.write_text(
            "\n".join(
                line.rstrip()
                for line in svg_path.read_text(encoding="utf-8").splitlines()
            )
            + "\n",
            encoding="utf-8",
        )
        plt.close(fig)


def compare(
    ap_files,
    reference_file,
    v2_metadata,
    v3_metadata,
    output,
    figure_dir,
    confidence=0.15,
):
    if confidence != 0.15:
        raise ValueError(
            "This comparison predeclares confidence 0.15; do not retune on test results."
        )
    targets = [output, output.with_suffix(".csv"), output.with_suffix(".json")]
    targets += [
        figure_dir / (name + "." + ext)
        for name in ("map_comparison_v3", "operating_point_comparison_v3")
        for ext in ("png", "svg", "pdf")
    ]
    if any(p.exists() for p in targets):
        raise FileExistsError("Comparison outputs already exist; choose fresh paths.")
    reference = json.loads(reference_file.read_text())
    records = reference["records"]
    if len(records) != 634 or sum(len(r["gt"]) for r in records) != 1819:
        raise ValueError("Unexpected benchmark reference size.")
    for record in records:
        image = Path(record["image"])
        parts = list(image.parts)
        index = len(parts) - 1 - parts[::-1].index("images")
        parts[index] = "labels"
        label = Path(*parts).with_suffix(".txt")
        if sha(image) != record["sha256"] or sha(label) != record["label_sha256"]:
            raise ValueError("Reference image or label changed: " + str(image))
    ap = {}
    for file in ap_files:
        with file.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                name = Path(row["model"]).stem
                if name in ap:
                    raise ValueError("Duplicate AP row for " + name)
                ap[name] = row
    if set(ap) != set(ORDER):
        raise ValueError("Expected exactly stock, v2 plain/storm, and v3 plain/storm.")
    metadata2 = json.loads(v2_metadata.read_text())
    metadata3 = json.loads(v3_metadata.read_text())
    checkpoint_metadata = {
        Path(item["file"]).stem: item for item in metadata2 + metadata3
    }
    if set(checkpoint_metadata) != set(ORDER[1:]):
        raise ValueError("Missing or unexpected fine-tuned checkpoint metadata.")
    for checkpoint in metadata2:
        if checkpoint["train_args"]["epochs"] != 50 or checkpoint["train_results"].get(
            "epoch"
        ) != list(range(1, 51)):
            raise ValueError("V2 metadata does not establish a 50-epoch run.")
    for checkpoint in metadata3:
        history = checkpoint["train_results"]
        if checkpoint["train_args"]["epochs"] != 15 or history.get("epoch") != list(
            range(1, 16)
        ):
            raise ValueError("V3 history does not establish a 15-epoch run.")
        if not all(
            math.isfinite(float(v)) for values in history.values() for v in values
        ):
            raise ValueError("Non-finite training history.")
    expected_protocol = {
        "split": "test",
        "classes": "person",
        "images": "634",
        "device": "cpu",
        "imgsz": "640",
        "batch": "1",
        "conf": "0.001",
        "iou": "0.7",
        "ultralytics_version": "8.4.178",
    }
    rows = []
    for name in ORDER:
        row = ap[name]
        if any(row[k] != v for k, v in expected_protocol.items()):
            raise ValueError("AP protocol mismatch for " + name)
        if Path(row["data"]).resolve() != Path(reference["test_data"]).resolve():
            raise ValueError("AP dataset differs from fixed-score reference.")
        model_hash = sha(row["model"])
        if (
            name in checkpoint_metadata
            and checkpoint_metadata[name]["sha256"] != model_hash
        ):
            raise ValueError("Comparison metadata does not match checkpoint " + name)
        artifacts = Path(row["artifacts"])
        provenance = json.loads((artifacts / "prediction_provenance.json").read_text())
        if (
            provenance["checkpoint_sha256"] != model_hash
            or provenance["data_yaml_sha256"] != sha(row["data"])
            or provenance["images"] != 634
        ):
            raise ValueError("AP prediction provenance differs for " + name)
        predictions = defaultdict(list)
        known = {r["file_name"] for r in records}
        for item in json.loads((artifacts / "predictions.json").read_text()):
            if item["category_id"] != 1 or item["file_name"] not in known:
                raise ValueError("Unexpected prediction category/image.")
            x, y, w, h = item["bbox"]
            predictions[item["file_name"]].append(
                [x, y, x + w, y + h, item["score"], 0]
            )
        tp = fp = fn = 0
        for record in records:
            found = match(
                predictions[record["file_name"]], record["gt"], [], confidence
            )
            tp += len(found["tp"])
            fp += len(found["fp"])
            fn += len(found["fn"])
        fixed = metrics(tp, fp, fn)
        if tp + fn != 1819:
            raise AssertionError("Ground-truth accounting mismatch.")
        result = {
            "model": name,
            "model_sha256": model_hash,
            "map50": float(row["map50"]),
            "map50_95": float(row["map50_95"]),
            "ap_summary_precision": float(row["precision"]),
            "ap_summary_recall": float(row["recall"]),
            "fixed_confidence": confidence,
            "fixed_precision": fixed["precision"],
            "fixed_recall": fixed["recall"],
            "fixed_f2": fixed["f2"],
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "inference_ms_per_image": float(row["inference_ms_per_image"]),
        }
        if not all(
            0 <= result[k] <= 1
            for k in (
                "map50",
                "map50_95",
                "fixed_precision",
                "fixed_recall",
                "fixed_f2",
            )
        ):
            raise ValueError("Invalid metric range.")
        rows.append(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "images": 634,
        "person_boxes": 1819,
        "rows": rows,
        "protocol": expected_protocol,
        "fixed_confidence": confidence,
        "threshold_origin": "Retained 0.15 comparison point agreed before v3 evaluation; not selected or optimized on v3 test results.",
        "ground_truth_reference_sha256": sha(reference_file),
        "ap_csv_sha256": {str(p): sha(p) for p in ap_files},
        "v3_checkpoint_metadata": [
            {
                "file": r["file"],
                "sha256": r["sha256"],
                "parameters": r["parameters"],
                "version": r["version"],
                "base": r["train_args"]["model"],
                "epochs": r["train_args"]["epochs"],
                "recorded_epochs": r["recorded_epochs"],
                "resume": r["train_args"].get("resume"),
                "argument_differences_from_v2": r["v2_argument_differences"],
            }
            for r in metadata3
        ],
        "historical_training_membership_verified": False,
        "deployment_changed": False,
        "limitations": [
            "Benchmark results do not certify independent unseen-scene performance.",
            "Training budget changed along with patience and recorded dropout; training package version differs.",
            "Dropout is classification-only in the installed Ultralytics code; its recorded value is not evidence of an active detection-model change.",
            "Fixed-confidence scores are greedy IoU 0.5 matches; AP summary precision/recall use the Ultralytics confidence curve.",
            "CPU timings are single-run values, not evidence of a speed improvement.",
        ],
    }
    output.with_suffix(".json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    plots(rows, figure_dir)
    by_name = {r["model"]: r for r in rows}

    def pct(value):
        return f"{100 * value:.2f}%"

    # The documented invocation writes the figures beside the report in docs/figures.
    import os

    link = lambda path: Path(os.path.relpath(path, output.parent)).as_posix()
    lines = [
        "# V3 model comparison",
        "",
        "Both supplied v3 weights are YOLO26s person detectors with 15 recorded epochs. All 634 image and annotation hashes match the v2 benchmark. Stock/v2 scores are reused from the existing matching-protocol evaluation; only v3 inference was rerun.",
        "",
        "## mAP comparison",
        "",
        f"![mAP comparison]({link(figure_dir / 'map_comparison_v3.png')})",
        "",
        "| Model | mAP50 | mAP50-95 | AP-summary precision | AP-summary recall |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['model']} | {pct(row['map50'])} | {pct(row['map50_95'])} | {pct(row['ap_summary_precision'])} | {pct(row['ap_summary_recall'])} |"
        )
    lines += ["", "## V3 changes relative to v2", ""]
    for kind in ("plain", "storm"):
        old = by_name[f"best_{kind}_v2"]
        new = by_name[f"best_{kind}_v3"]
        lines.append(
            f"- **{kind.title()}:** mAP50 {(new['map50'] - old['map50']) * 100:+.2f} percentage points; mAP50-95 {(new['map50_95'] - old['map50_95']) * 100:+.2f} percentage points."
        )
    lines += [
        "",
        "These are measured benchmark differences, not proof of an epoch-only causal effect. Both v3 checkpoints record `resume=false` and base `yolo26s.pt`: these were fresh 15-epoch runs. Compared with v2, recorded patience changes from 10 to 5 and dropout from 0.0 to 0.1, alongside run names and training package version (8.4.181 to 8.4.182). Dropout is classification-only in the installed evaluator; no dropout module effect on these detectors is established. The team reported using the same training datasets.",
        "",
        "## Fixed confidence comparison",
        "",
        f"![Precision, recall and F2]({link(figure_dir / 'operating_point_comparison_v3.png')})",
        "",
        "Every model uses confidence **0.15**, chosen before viewing v3 results. This includes stock; the earlier stock operating-point table used 0.05, so its numbers differ. No v3 threshold sweep or optimization was performed.",
        "",
        "| Model | Precision | Recall | F2 | Matched boxes | Unmatched predictions | Missed boxes |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['model']} | {pct(row['fixed_precision'])} | {pct(row['fixed_recall'])} | {pct(row['fixed_f2'])} | {row['tp']} | {row['fp']} | {row['fn']} |"
        )
    lines += [
        "",
        "## Interpretation and artifacts",
        "",
        "- AP protocol: CPU, FP32, eight threads, image size 640, batch 1, candidate floor 0.001, NMS IoU 0.7, no augmentation, Ultralytics 8.4.178. V3 load/inference succeeded despite its newer recorded training version.",
        "- Fixed metrics use confidence-order one-to-one matching at IoU 0.5 against the same 1,819 boxes. Unmatched predictions are relative to available labels, not independently verified false alarms.",
        "- These charts report completed detection-quality measurements on the 634-image benchmark. They do not measure a speed improvement; CPU timing excludes phone upload and speech.",
        f"- [Comparison CSV]({output.with_suffix('.csv').name}), [compact JSON]({output.with_suffix('.json').name}), and [fresh v3 AP CSV](results_v3.csv).",
        f"- mAP: [PNG]({link(figure_dir / 'map_comparison_v3.png')}), [SVG]({link(figure_dir / 'map_comparison_v3.svg')}), [PDF]({link(figure_dir / 'map_comparison_v3.pdf')}).",
        f"- Precision/recall/F2: [PNG]({link(figure_dir / 'operating_point_comparison_v3.png')}), [SVG]({link(figure_dir / 'operating_point_comparison_v3.svg')}), [PDF]({link(figure_dir / 'operating_point_comparison_v3.pdf')}).",
        "- Raw predictions, recovered histories, full checkpoint metadata and input-reference checks are under `runs/post_training_v3/` (shared separately). Existing v2 weights, charts and CSVs were preserved. No running server configuration was changed.",
        "",
    ]
    output.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(rows, indent=2))
    print("Saved " + str(output) + " and both chart sets.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ap-results", type=Path, nargs="+", required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--v2-metadata", type=Path, required=True)
    parser.add_argument("--v3-metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--figure-dir", type=Path, required=True)
    args = parser.parse_args()
    compare(
        args.ap_results,
        args.reference,
        args.v2_metadata,
        args.v3_metadata,
        args.output,
        args.figure_dir,
    )
