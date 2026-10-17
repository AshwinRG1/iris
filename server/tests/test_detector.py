import pytest
from PIL import Image

from detector import ZeroShotDetector, choose_model, pick_device, to_normalized_box
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
    seen: list[object] = []

    def fake_detect(image: object) -> list[dict[str, object]]:
        seen.append(image)
        return [{"label": "person", "conf": 0.8, "box": [0.1, 0.2, 0.3, 0.4]}]

    image = Image.new("RGB", (8, 8))
    detector = ZeroShotDetector(fake_detect, device="cuda")
    assert detector.load() == "cuda"
    [d] = detector.detect(image)
    assert seen == [image]
    assert (d.label, d.conf) == ("person", 0.8)
    _assert_box(d.box, (0.1, 0.2, 0.3, 0.4))


def test_zero_shot_adapter_clamps_boxes_and_handles_no_results() -> None:
    def fake_detect(image: object) -> list[dict[str, object]]:
        return [{"label": "x", "conf": 0.5, "box": [-0.1, 0.0, 1.2, 1.0]}]

    [d] = ZeroShotDetector(fake_detect, device="cpu").detect(Image.new("RGB", (8, 8)))
    _assert_box(d.box, (0.0, 0.0, 1.0, 1.0))
    assert ZeroShotDetector(lambda image: [], device="cpu").detect(Image.new("RGB", (8, 8))) == []
