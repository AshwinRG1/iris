from detector import to_normalized_box
from domain import Box


def test_pixel_box_is_divided_by_image_size() -> None:
    assert to_normalized_box((64.0, 48.0, 320.0, 240.0), width=640, height=480) == Box(
        0.1, 0.1, 0.5, 0.5
    )


def test_pixel_box_is_clamped_to_the_image() -> None:
    assert to_normalized_box((-5.0, -5.0, 700.0, 500.0), width=640, height=480) == Box(
        0.0, 0.0, 1.0, 1.0
    )
