"""Freeze provisional validation F2 operating points from a completed failure run."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def choose(rows, minimum_precision=0):
    candidates = [
        dict(row, confidence=float(t))
        for t, row in rows.items()
        if row["precision"] >= minimum_precision and row["tp"] > 0
    ]
    if not candidates:
        raise ValueError("No positive operating point meets the precision requirement.")
    return max(
        candidates,
        key=lambda r: (r["f2"], r["recall"], r["precision"], r["confidence"]),
    )


def select(source, output, minimum_precision=0):
    if output.exists():
        raise FileExistsError(output)
    combined = json.loads((source / "summary.json").read_text())
    manifest = json.loads((source / "dataset_manifest.json").read_text())
    points = []
    for name, m in combined["models"].items():
        path = Path(m["model"]).resolve()
        if sha(path) != m["sha256"]:
            raise ValueError("Checkpoint changed since validation: " + str(path))
        point = choose(m["overall"], minimum_precision)
        points.append(
            {
                "name": name,
                "model": str(path),
                "model_sha256": m["sha256"],
                "confidence": point["confidence"],
                "validation_metrics": point,
            }
        )
    result = {
        "schema": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "selection_split": "val",
        "status": "provisional_original_export_validation",
        "validation_provenance_verified": False,
        "deployment_approved": False,
        "selection_rule": "Maximum F2 on supplied development validation grid; tie break recall, precision, confidence.",
        "minimum_precision": minimum_precision,
        "source_run": str(source.resolve()),
        "source_summary_sha256": sha(source / "summary.json"),
        "settings": manifest["settings"],
        "validation_image_sha256": [r["image_sha256"] for r in manifest["records"]],
        "models": points,
        "limitation": "C actual v2 merged validation membership and source/video separation are unverified. This is a frozen provisional comparison, not production threshold approval.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({"status": result["status"], "models": points}, indent=2), flush=True
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-run", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--min-precision", type=float, default=0)
    a = p.parse_args()
    if not 0 <= a.min_precision <= 1:
        p.error("min precision must be between zero and one")
    select(a.source_run, a.output, a.min_precision)
