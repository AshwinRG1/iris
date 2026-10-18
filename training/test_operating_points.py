"""Regression checks for validation selection and leakage-resistant fixed scoring."""

import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
import yaml
from select_operating_points import choose
from score_operating_points import score


class SelectionTests(unittest.TestCase):
    def test_f2_not_maximum_recall_selects_threshold(self):
        rows = {
            "0.05": {"tp": 9, "precision": 0.1, "recall": 0.9, "f2": 0.35},
            "0.15": {"tp": 8, "precision": 0.8, "recall": 0.8, "f2": 0.8},
        }
        self.assertEqual(choose(rows)["confidence"], 0.15)
        self.assertEqual(choose(rows, 0.5)["confidence"], 0.15)
        with self.assertRaises(ValueError):
            choose(rows, 0.95)

    def test_no_true_positives_cannot_freeze_a_threshold(self):
        with self.assertRaises(ValueError):
            choose({"0.1": {"tp": 0, "precision": 0, "recall": 0, "f2": 0}})


class FrozenScoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "test/images").mkdir(parents=True)
        (self.root / "test/labels").mkdir()
        self.image = self.root / "test/images/frame.jpg"
        Image.new("RGB", (10, 10)).save(self.image)
        (self.root / "test/labels/frame.txt").write_text("0 0.5 0.5 0.6 0.6\n")
        self.data = self.root / "data.yaml"
        self.data.write_text(
            yaml.safe_dump(
                {"path": str(self.root), "test": "test/images", "names": ["person"]}
            )
        )
        self.model = self.root / "weights.pt"
        self.model.write_bytes(b"fake weights for hash-only test")
        self.selection = self.root / "selection.json"
        self.point = {
            "schema": 1,
            "selection_split": "val",
            "validation_image_sha256": ["different-image"],
            "validation_provenance_verified": False,
            "models": [
                {
                    "name": "model",
                    "model": str(self.model),
                    "model_sha256": hashlib.sha256(self.model.read_bytes()).hexdigest(),
                    "confidence": 0.15,
                }
            ],
        }
        self.write_selection()
        artifact = self.root / "artifacts"
        artifact.mkdir()
        (artifact / "predictions.json").write_text(
            json.dumps(
                [
                    {
                        "file_name": "frame.jpg",
                        "category_id": 1,
                        "bbox": [2, 2, 6, 6],
                        "score": 0.2,
                    }
                ]
            )
        )
        provenance = {
            "checkpoint_sha256": self.point["models"][0]["model_sha256"],
            "data_yaml_sha256": hashlib.sha256(self.data.read_bytes()).hexdigest(),
            "split": "test",
            "images": 1,
            "imgsz": 640,
            "nms_iou": 0.7,
            "conf": 0.001,
            "rect": False,
            "half": False,
            "augment": False,
        }
        (artifact / "prediction_provenance.json").write_text(json.dumps(provenance))
        self.ap = self.root / "ap.csv"
        with self.ap.open("w", newline="") as f:
            w = csv.DictWriter(
                f, fieldnames=["model", "images", "data", "split", "artifacts"]
            )
            w.writeheader()
            w.writerow(
                {
                    "model": str(self.model),
                    "images": 1,
                    "data": str(self.data),
                    "split": "test",
                    "artifacts": str(artifact),
                }
            )
        self.output = self.root / "fixed.csv"

    def read_row(self):
        with self.output.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))[0]

    def write_selection(self):
        self.selection.write_text(json.dumps(self.point))

    def test_selected_threshold_is_applied_without_test_optimization(self):
        self.point["models"][0]["confidence"] = 0.3
        self.write_selection()
        score(self.data, self.ap, self.selection, self.output)
        row = self.read_row()
        self.assertEqual(row["tp"], "0")
        self.assertEqual(row["fn"], "1")
        self.assertEqual(float(row["confidence"]), 0.3)

    def test_matching_and_f2_export(self):
        score(self.data, self.ap, self.selection, self.output)
        row = self.read_row()
        self.assertEqual(row["tp"], "1")
        self.assertEqual(float(row["f2"]), 1)

    def test_changed_checkpoint_is_rejected(self):
        self.model.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "hash changed"):
            score(self.data, self.ap, self.selection, self.output)
        self.assertFalse(self.output.exists())

    def test_validation_test_overlap_is_rejected(self):
        self.point["validation_image_sha256"] = [
            hashlib.sha256(self.image.read_bytes()).hexdigest()
        ]
        self.write_selection()
        with self.assertRaisesRegex(ValueError, "overlaps"):
            score(self.data, self.ap, self.selection, self.output)

    def test_test_selected_thresholds_are_rejected(self):
        self.point["selection_split"] = "test"
        self.write_selection()
        with self.assertRaisesRegex(ValueError, "validation"):
            score(self.data, self.ap, self.selection, self.output)


if __name__ == "__main__":
    unittest.main()
