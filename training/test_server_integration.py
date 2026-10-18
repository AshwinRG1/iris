"""Guard against silent model fallback and broken phone geometry in HTTP checks."""

import copy
import unittest
from check_server_integration import verify_frame, verify_health


class IntegrationContractTests(unittest.TestCase):
    def setUp(self):
        self.health = {
            "ready": True,
            "load_error": None,
            "fallback": None,
            "model_file": "best_plain_v2.pt",
            "model_sha256": "expected",
            "class_names": ["person"],
        }
        self.reply = {
            "frame_id": 1,
            "server_ms": 120,
            "alert": None,
            "description": None,
            "description_id": None,
            "detections": [
                {
                    "label": "person",
                    "conf": 0.8,
                    "side": "left",
                    "box": {"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.4},
                    "center": {"x": 0.2, "y": 0.4},
                }
            ],
        }

    def test_requested_checkpoint_and_frame_pass(self):
        verify_health(self.health, "best_plain_v2.pt", "expected")
        self.assertEqual(verify_frame(self.reply, 0), 1)

    def test_stock_fallback_rejected_even_when_ready(self):
        self.health["fallback"] = "load_failed: FileNotFoundError"
        with self.assertRaisesRegex(ValueError, "fell back"):
            verify_health(self.health, "best_plain_v2.pt", "expected")

    def test_wrong_checkpoint_hash_rejected(self):
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            verify_health(self.health, "best_plain_v2.pt", "another")

    def test_invalid_geometry_or_direction_rejected(self):
        for field, value in [
            ("box", {"x": 0.9, "y": 0.2, "w": 0.2, "h": 0.4}),
            ("side", "right"),
            ("conf", float("nan")),
        ]:
            reply = copy.deepcopy(self.reply)
            reply["detections"][0][field] = value
            with self.assertRaises(ValueError):
                verify_frame(reply, 0)

    def test_stale_frame_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "advance"):
            verify_frame(self.reply, 1)

    def test_empty_detections_valid_without_speech(self):
        self.reply["detections"] = []
        self.assertEqual(verify_frame(self.reply, 0), 1)


if __name__ == "__main__":
    unittest.main()
