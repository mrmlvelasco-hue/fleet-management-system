"""Shrinking an uploaded image before it is stored.

Images are the single biggest driver of attachment-table growth: a
phone photo can run 4000x3000px / several MB, while nothing in this
system ever needs more than a sane working resolution on screen or on
a printed form. Capping the pixel dimensions before the bytes reach
`file_data` is what actually limits database growth -- the old
IMG_*_PX parameters (one per hardcoded VEMS-era category: vehicle
front/back, CR, person/ATD, LTO/engine, engine CR) were seeded but
never read by any upload path, so they limited nothing.
"""
import io

import pytest

from app.core.attachments.image_resize import shrink_to_max_dimension


def _image_bytes(width, height, fmt="PNG", color=(200, 210, 220)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, fmt)
    return buf.getvalue()


def test_oversized_png_is_shrunk_to_the_limit():
    content = _image_bytes(3000, 2000, "PNG")
    result = shrink_to_max_dimension(content, 800, mime_type="image/png")
    from PIL import Image
    with Image.open(io.BytesIO(result)) as im:
        assert max(im.size) <= 800
    assert len(result) < len(content)


def test_oversized_jpeg_is_shrunk_and_stays_jpeg():
    content = _image_bytes(2400, 1600, "JPEG")
    result = shrink_to_max_dimension(content, 640, mime_type="image/jpeg")
    from PIL import Image
    with Image.open(io.BytesIO(result)) as im:
        assert max(im.size) <= 640
        assert im.format == "JPEG"


def test_image_already_within_limit_is_returned_unchanged():
    content = _image_bytes(300, 200, "PNG")
    result = shrink_to_max_dimension(content, 800, mime_type="image/png")
    assert result == content


def test_non_image_mime_type_is_returned_unchanged():
    content = b"%PDF-1.4 not really a pdf but doesn't matter here"
    result = shrink_to_max_dimension(content, 800, mime_type="application/pdf")
    assert result == content


def test_missing_mime_type_is_returned_unchanged():
    content = _image_bytes(3000, 2000, "PNG")
    result = shrink_to_max_dimension(content, 800, mime_type=None)
    assert result == content


def test_corrupt_image_bytes_never_raises_and_is_returned_unchanged():
    content = b"\x00\x01\x02 not actually an image"
    result = shrink_to_max_dimension(content, 800, mime_type="image/png")
    assert result == content


def test_zero_or_missing_max_dimension_is_a_no_op():
    content = _image_bytes(3000, 2000, "PNG")
    assert shrink_to_max_dimension(content, 0, mime_type="image/png") == content
    assert shrink_to_max_dimension(content, None, mime_type="image/png") == content


def test_empty_content_is_returned_unchanged():
    assert shrink_to_max_dimension(b"", 800, mime_type="image/png") == b""
