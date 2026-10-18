"""Render the completed v2 checkpoint, dataset, threshold, test and adapter checks."""

import argparse
import csv
import json
import os
from pathlib import Path


def render(root, ap_file, fixed_file, report, http_results=None):
    metadata = json.loads((root / "checkpoint_metadata.json").read_text())
    training = json.loads((root / "training_history_checks.json").read_text())
    audit = json.loads((root / "original_split_audit.json").read_text())
    validation = json.loads((root / "validation_diagnostic/summary.json").read_text())
    selection = json.loads((root / "operating_points.json").read_text())
    clips = json.loads((root / "clip_checks/summary.json").read_text())
    with ap_file.open(newline="", encoding="utf-8") as f:
        ap = list(csv.DictReader(f))
    with fixed_file.open(newline="", encoding="utf-8") as f:
        fixed = list(csv.DictReader(f))
    if len(ap) != 3 or len(fixed) != 3 or len(clips["rows"]) != 21:
        raise ValueError("Incomplete comparison or clip checks.")
    candidate = max(selection["models"], key=lambda p: p["validation_metrics"]["f2"])
    summary = {
        "created_from_run": str(root.resolve()),
        "weights": [
            {k: v for k, v in m.items() if k not in ["train_results", "train_args"]}
            | {
                "training_arguments": m["train_args"],
                "recorded_epochs": len(m["train_results"]["epoch"]),
            }
            for m in metadata
        ],
        "training_checks": training,
        "source_overlap_audit": {
            k: (len(v) if isinstance(v, list) else v) for k, v in audit.items()
        },
        "ap_results": ap,
        "fixed_operating_point_results": fixed,
        "validation_thresholds": {
            name: m["overall"] for name, m in validation["models"].items()
        },
        "provisional_candidate_from_development_f2": candidate["name"],
        "clip_adapter_checks": clips,
        "http_integration_checks": json.loads(http_results.read_text())
        if http_results
        else None,
        "remaining": [
            "C exact merged train/validation manifest and held-out exclusions",
            "Near-duplicate/source-video split audit",
            "Final acceptance against B deployed/demo server",
            "iPhone/mock-camera HUD and Gemini-description speech loop with A",
            "Final survivor-target clip annotations and source permissions where unknown",
        ],
        "deployment_approved": False,
        "validation_provenance_verified": False,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.with_suffix(".json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    def pct(v):
        return f"{float(v) * 100:.2f}%"

    def link(path):
        return Path(os.path.relpath(path, report.parent)).as_posix()

    lines = [
        "# V2 post-training validation - October 18, 2026",
        "",
        "**Both v2 files load correctly and contain all 50 recorded training epochs. The measured v2 benchmark results improve on stock YOLO26s. Split provenance and final phone/glasses testing remain unresolved, so this is not deployment approval.**",
        "",
        "## Completed checks",
        "",
        "- Copied `best_plain_v2.pt` and `best_storm_v2.pt` from Downloads into `server/weights/`; source/destination hashes matched. Earlier weights were preserved. All `.pt` files remain ignored by Git.",
        "- Both identify `yolo26s.pt` as the training base and detect `person` at class ID 0. They contain 9,948,638 parameters before inference fusion; the one-class head differs normally from stock COCO output.",
        "- Both embedded histories contain epochs 1 through 50 with finite values in all recorded columns. The previous missing-epoch concern is resolved by the saved history. The stripped checkpoint epoch field is -1 and does not mean no training occurred.",
        "- Recovered training-history CSVs, training-argument YAMLs, checkpoint metadata, and curves from the weights. These are recovered records, not original run-folder files.",
        "- Original-export development diagnostic completed for all 1,807 images / 9,365 person boxes on stock and both v2 models. All six thresholds include TP/FP/FN, precision, recall, F2, source classes, and size breakdowns.",
        "- Froze a single provisional threshold per model before opening this test comparison. Test thresholds were not swept or retuned.",
        "- Compared all three models on the same corrected 634-image / 1,819-box test dataset. Exported person predictions with checkpoint/dataset/settings provenance while preserving the ordinary Ultralytics AP calculation.",
        "- Exercised the actual server `YoloDetector` adapter and pure alert policy on seven clips for every model. These are offline subsystem checks, not HTTP or phone tests.",
        "",
        "## Critical split/provenance limitation",
        "",
        f"The {audit['source_training_images_checked']:,} original source-training images contain **{len(audit['exact_source_train_held_out_matches'])} byte-identical matches to the 634-image test set**. They contain {len(audit['exact_source_train_validation_matches'])} byte-identical matches to the original validation folders. The diagnostic validation and test images do not overlap byte-for-byte.",
        "",
        "The team previously reported excluding the 634 test images from actual training and validation. The original exports retaining those images does not itself prove leakage into C's v2 runs. However, `/content/data/merged` and `/content/data/merged_storm` are not available locally, so the actual exclusions cannot be independently verified from these weights. C needs to supply the exact merged split manifest or dataset snapshot. Until then, treat test results as a conditional benchmark, not proof of independent generalization. Near-duplicate and neighboring-frame/source-video separation remain unverified.",
        "",
        "The original validation annotations also retain the missing-label/box-convention concerns documented in [the failure report](FAILURE_REPORT.md). Selected thresholds remain explicitly provisional. No automatic server model swap was performed.",
        "",
        "## Test AP comparison",
        "",
        "All rows: 634 images, 1,819 boxes, CPU, FP32, image size 640, batch 1, candidate floor 0.001, NMS IoU 0.7, eight threads, Ultralytics 8.4.178. Training checkpoints record Ultralytics 8.4.181; load and inference succeeded under the installed evaluator. Stock mAP reproduced the earlier result exactly.",
        "",
        "| Model | AP-summary precision | AP-summary recall | mAP50 | mAP50-95 | Inference ms/image |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in ap:
        lines.append(
            f"| {Path(r['model']).stem} | {pct(r['precision'])} | {pct(r['recall'])} | {pct(r['map50'])} | {pct(r['map50_95'])} | {float(r['inference_ms_per_image']):.1f} |"
        )
    lines += [
        "",
        "The precision/recall above are Ultralytics confidence-curve summaries, not the deployed alert operating point. Timings are single-run measurements and exclude upload/speech; they are not a controlled hardware ranking. The JSON export was checked on a development image with saving enabled/disabled: the four ordinary AP-summary values matched exactly.",
        "",
        "## Frozen operating-point test results",
        "",
        "| Model | Frozen confidence | Precision | Recall | F2 | Matched boxes | Unmatched predictions | Missed boxes |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in fixed:
        lines.append(
            f"| {Path(r['model']).stem} | {r['confidence']} | {pct(r['precision'])} | {pct(r['recall'])} | {pct(r['f2'])} | {r['tp']} | {r['fp']} | {r['fn']} |"
        )
    lines += [
        "",
        "These are greedy confidence-order, one-to-one person matches at IoU 0.5 using saved AP-validator predictions. They are not mAP, spoken false alerts, or survivor-specific clip accuracy. FP means unmatched against the available labels; incomplete labels can overstate false positives. Prediction retention can differ from the development `predict` path, so selection is a provisional operating point, not proof of a globally optimal threshold.",
        "",
        "## Development threshold choice",
        "",
        "| Model | Chosen confidence | Development precision | Development recall | Development F2 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for p in selection["models"]:
        m = p["validation_metrics"]
        lines.append(
            f"| {p['name']} | {p['confidence']} | {pct(m['precision'])} | {pct(m['recall'])} | {pct(m['f2'])} |"
        )
    lines += [
        "",
        f"The provisional candidate under the predeclared development F2 rule is **{candidate['name']}**. Plain and storm are close; this run does not establish a statistically significant difference between them or prove a storm-augmentation benefit. This candidate was selected from development data, not from the test outcome. Resolve the actual split and annotation questions before production approval.",
        "",
        "## Offline clip integration",
        "",
        "Every model/clip run starts with fresh alert state. Inputs emulate 640-pixel JPEG70 frames at nominal 2.5 fps, using the frozen model confidence for alert hits and the server's existing streak/cooldown policy. Nominal clip time is used; request-arrival delays and skipped-upload behavior are not simulated.",
        "",
        "| Model | Clip | Sampled frames | Frames with person detections | Alerts | Detector p95 ms |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for r in clips["rows"]:
        lines.append(
            f"| {r['model']} | {r['clip']} | {r['frames']} | {r['person_frames']} | {len(r['alerts'])} | {r['p95_detector_ms']:.1f} |"
        )
    lines += [
        "",
        "Normalized boxes, finite confidence values, person labels, checkpoint identity, and direction values passed the adapter contract checks. Detections/alerts are counts, not accuracy: survivor boxes and some timing annotations remain unfinalized. The cropped no-person candidate is useful for reviewing false alerts, but it has not replaced a fully reviewed independent negative-video benchmark.",
        "",
        "## Still required from C, B, and A",
        "",
        "- **C:** share the exact v2 merged train/validation manifest or data snapshot; verify the 634-image exclusion, source/video grouping, near duplicates, actual class remapping, and augmentation-label integrity. The weights alone cannot establish these.",
        "- **D:** approve survivor-target boxes/intervals for clip scoring. Keep the evaluation clips out of future training/tuning, or replace them if used for development.",
        "- **B:** run the chosen candidate on the real server and confirm `/health` reports its filename/hash, `ready=true`, and `fallback=null`. Isolated local HTTP checks are recorded below when supplied; B still needs to verify the actual demo host.",
        "- **A and B:** run the iPhone/mock-camera loop, inspect actual survivor boxes in the server live view, confirm spoken Gemini descriptions and their directions (alert text is display-only), measure network/round-trip latency, false alerts, and upload timeouts. A physical iPhone/glasses path was not available here.",
        "",
        "For B, the candidate path is `weights/"
        + candidate["name"]
        + ".pt` when launching from `server/`, or its absolute path. Set both `CONF_DETECT` and `CONF_ALERT` to the corresponding experimental threshold of "
        + str(candidate["confidence"])
        + ". It is a development recommendation only; do not interpret the offline counts as deployment approval.",
        "",
        "## Artifacts and reproduction",
        "",
        f"- [AP CSV]({link(ap_file)}) and [frozen-threshold CSV]({link(fixed_file)}).",
        "- [Validation six-threshold table](validation_v2_thresholds.csv) and [compact combined JSON]("
        + report.with_suffix(".json").name
        + ").",
        f"- [Recovered training curves]({link(root / 'training_curves.png')}) and [validation threshold curves]({link(root / 'validation_threshold_curves.png')}).",
        f"- [Development failure gallery]({link(root / 'validation_diagnostic/gallery.html')}) and [offline adapter snapshots]({link(root / 'clip_checks/gallery.html')}).",
        f"- [Full checkpoint metadata]({link(root / 'checkpoint_metadata.json')}), [source overlap audit]({link(root / 'original_split_audit.json')}), and [frozen operating points]({link(root / 'operating_points.json')}).",
        "- Recovered histories/configurations, per-frame clip logs, raw predictions, and full manifests are under `runs/post_training_v2/`. Share these ignored artifacts and weights through the team's data-sharing workflow; Git contains code and compact reports only.",
        "- [Evaluation workflow and commands](../training/EVALUATION.md) explain reruns. Use fresh output folders/CSVs, freeze thresholds before test scoring, and preserve checkpoint hashes.",
        "",
        "No training labels, source images, previous checkpoints, historical result CSV, or production server configuration were replaced. The v2 weights and large artifacts remain local/shared separately. The software and compact reports are intended for PR review; the production server was not redeployed by these checks.",
        "",
    ]
    if http_results:
        http = summary["http_integration_checks"]
        if not http.get("rows") or not all(row.get("passed") for row in http["rows"]):
            raise ValueError("HTTP record is incomplete or contains failed checks.")
        lines += [
            "## Additional real HTTP verification",
            "",
            "Dedicated local Python 3.12 servers loaded both v2 models without fallback. Each accepted six JPEG requests and passed checkpoint/hash, frame geometry, and live-view endpoint checks. The full server suite passed 347 tests, the evaluation suite passed 33, and server Ruff/mypy checks passed.",
            "",
            "Plain v2 also completed unscored aerial, rainy-lake and negative clip replays with zero HTTP/authentication errors. One upload slot was skipped per clip. An event still occurred in the negative candidate; do not treat transport success as accuracy. Both servers used detection/alert confidence 0.15, CPU, and descriptions off.",
            "",
            "The current app speaks Gemini descriptions once per description ID; directional alert text is display-only. These local HTTP checks do not test Gemini, the phone/glasses HUD, audible speech, or B's actual deployment.",
            "",
            f"See [HTTP results]({link(http_results)}) and [MVP handoff](MVP_HANDOFF.md) for identities, timings, reproduction, and remaining manual acceptance.",
            "",
        ]
    report.write_text("\n".join(lines), encoding="utf-8")
    print("Wrote " + str(report), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--ap-results", type=Path, required=True)
    p.add_argument("--fixed-results", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--http-results", type=Path)
    a = p.parse_args()
    render(a.run, a.ap_results, a.fixed_results, a.report, a.http_results)
