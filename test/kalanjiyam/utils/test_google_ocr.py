"""Tests for OCR shared types."""

from kalanjiyam.utils.ocr_types import post_process, serialize_bounding_boxes


def test_post_process():
    text = """हरिः |
हरिः ||
हरिः ।।
“Hello world”
‘Hello world’"""
    actual = post_process(text)
    assert (
        actual
        == """हरिः ।
हरिः ॥
हरिः ॥
"Hello world"
'Hello world'"""
    )


def test_serialize_bounding_boxes():
    boxes = [
        (0, 0, 100, 20, "word"),
        (120, 25, 300, 45, "another"),
    ]
    blob = "0\t0\t100\t20\tword\n120\t25\t300\t45\tanother"
    assert serialize_bounding_boxes("google", boxes) == blob
