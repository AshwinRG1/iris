import pytest

from detector import to_normalized_box
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
