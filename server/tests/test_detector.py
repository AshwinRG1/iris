from collections.abc import Callable
from typing import Any

import pytest
from PIL import Image

from detector import (
    ZeroShotDetector,
    choose_model,
    normalize_class_names,
    pick_device,
    to_normalized_box,
)
from domain import Box


def _assert_box(actual: Box, expected: tuple[float, float, float, float]) -> None:
    assert (actual.x1, actual.y1, actual.x2, actual.y2) == pytest.approx(expected)


def test_pixel_box_is_divided_by_image_size() -> None:
    _assert_box(
        to_normalized_box((64.0, 48.0, 320.0, 240.0), width=640, height=480), (0.1, 0.1, 0.5, 0.5)
    )


def test_non_square_image_divides_x_by_width_and_y_by_height() -> None:
    # Swapping width and height would give (0.2, 0.125, 0.6, 0.625) here.
    _assert_box(
        to_normalized_box((100.0, 50.0, 300.0, 250.0), width=400, height=500),
        (0.25, 0.1, 0.75, 0.5),
    )


def test_pixel_box_is_clamped_to_the_image() -> None:
    _assert_box(
        to_normalized_box((-5.0, -5.0, 700.0, 500.0), width=640, height=480), (0.0, 0.0, 1.0, 1.0)
    )


def test_onnx_is_served_on_cpu_even_when_a_gpu_exists() -> None:
    assert pick_device("", cuda=True, mps=True, onnx=True) == "cpu"
    assert pick_device("", cuda=True, mps=False) == "cuda"
    assert pick_device("mps", cuda=False, mps=False, onnx=True) == "mps"


def test_model_with_an_alert_label_is_used() -> None:
    assert choose_model(["person", "car"], frozenset({"person"})) == "use"


def test_model_with_one_of_several_alert_labels_is_used() -> None:
    assert choose_model(["car", "stairs"], frozenset({"person", "stairs"})) == "use"


def test_model_with_no_alert_label_falls_back() -> None:
    assert choose_model(["car", "dog"], frozenset({"person"})) == "fall_back"


def test_label_match_is_exact_not_substring() -> None:
    assert choose_model(["personal_item"], frozenset({"person"})) == "fall_back"


def test_model_with_no_classes_falls_back() -> None:
    assert choose_model([], frozenset({"person"})) == "fall_back"


def test_zero_shot_adapter_turns_dicts_into_detections() -> None:
    seen: list[Image.Image] = []
    image = Image.new("RGB", (8, 8))

    def fake_detect(img: Image.Image) -> list[dict[str, Any]]:
        seen.append(img)
        return [{"label": "person", "conf": 0.8, "box": [0.1, 0.2, 0.3, 0.4]}]

    detector = _zero_shot(fake_detect, device="cuda")
    assert detector.load().device == "cuda"
    seen.clear()  # drop the warmup call
    [d] = detector.detect(image)
    assert seen == [image]
    assert (d.label, d.conf) == ("person", 0.8)
    _assert_box(d.box, (0.1, 0.2, 0.3, 0.4))


def _zero_shot(fn: Callable[[Image.Image], list[dict[str, Any]]], device: str = "cpu") -> Any:
    detector = ZeroShotDetector(lambda: fn, device, resolve_device=lambda d: d)
    detector.load()
    return detector


def _one(box: Any, conf: Any = 0.5) -> list[Any]:
    detector = _zero_shot(lambda img: [{"label": "x", "conf": conf, "box": box}])
    return list(detector.detect(Image.new("RGB", (8, 8))))


def test_zero_shot_box_with_swapped_corners_is_sorted() -> None:
    [d] = _one([0.5, 0.9, 0.1, 0.2])
    _assert_box(d.box, (0.1, 0.2, 0.5, 0.9))


def test_zero_shot_box_outside_the_unit_square_is_rejected_not_clamped() -> None:
    assert _one([-0.1, 0.0, 0.5, 1.0]) == []
    assert _one([0.0, 0.0, 1.2, 1.0]) == []


def test_zero_shot_non_finite_numbers_are_skipped() -> None:
    nan, inf = float("nan"), float("inf")
    assert _one([0.1, 0.1, nan, 0.5]) == []
    assert _one([0.1, 0.1, inf, 0.5]) == []
    assert _one([0.1, 0.1, 0.5, 0.5], conf=nan) == []


def test_zero_shot_malformed_items_are_skipped_and_good_ones_kept() -> None:
    good = {"label": "person", "conf": 0.7, "box": [0.1, 0.1, 0.2, 0.2]}
    items: list[dict[str, Any]] = [
        {"label": "x", "box": [0.1, 0.1, 0.2, 0.2]},  # no conf
        {"label": "x", "conf": "high", "box": [0.1, 0.1, 0.2, 0.2]},  # not a number
        {"label": "x", "conf": 0.5, "box": [0.1, 0.1, 0.2]},  # 3 corners
        {"label": "x", "conf": 0.5, "box": None},
        {"label": "x", "conf": 0.5},  # no box
        good,
    ]
    detector = _zero_shot(lambda img: items)
    assert [d.label for d in detector.detect(Image.new("RGB", (8, 8)))] == ["person"]


def test_zero_shot_conf_is_clamped_to_unit_range() -> None:
    [d] = _one([0.1, 0.1, 0.2, 0.2], conf=1.4)
    assert d.conf == 1.0
    [d] = _one([0.1, 0.1, 0.2, 0.2], conf=-0.2)
    assert d.conf == 0.0


def test_zero_shot_malformed_items_are_logged_once_per_problem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    detector = _zero_shot(lambda img: [{"label": "x"}])
    for _ in range(3):
        detector.detect(Image.new("RGB", (8, 8)))
    assert len([r for r in caplog.records if "malformed" in r.getMessage()]) == 1


def test_zero_shot_no_results_is_an_empty_list() -> None:
    assert _zero_shot(lambda img: []).detect(Image.new("RGB", (8, 8))) == []


def test_class_names_come_out_in_id_order_from_a_dict_or_a_list() -> None:
    assert normalize_class_names({1: "car", 0: "person"}) == ("person", "car")
    assert normalize_class_names(["person", "car"]) == ("person", "car")


def test_label_check_ignores_case() -> None:
    assert choose_model(["Person"], frozenset({"person"})) == "use"
    assert choose_model(["person"], frozenset({"PERSON"})) == "use"
