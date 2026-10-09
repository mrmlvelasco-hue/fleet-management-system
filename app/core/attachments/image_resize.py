"""Shrink an uploaded image to a configurable maximum pixel dimension.

Images are what actually drives attachment-table growth. A phone photo
of a plate, an OR/CR, a driver's licence, or an odometer can run several
thousand pixels across and several MB; nothing in this system displays
or prints an attachment anywhere near that size. Capping the pixel
dimensions before the bytes reach `Attachment.file_data` is what limits
database growth -- rejecting large files outright (ATTACHMENT_MAX_SIZE_MB)
only catches the extreme cases and makes the person's upload fail instead
of simply shrinking it.

This replaces the old VEMS-era IMG_VEHICLE_FRONT_BACK_PX / IMG_CR_PX /
IMG_PERSON_ATD_PX / IMG_LTO_ENGINE_PX / IMG_ENGINE_CR_PX parameters, which
were one hardcoded limit per picture CATEGORY and were seeded but never
read by any upload path (document types are admin-configurable via Lookup
Maintenance, not a fixed set of categories, so there was never a matching
code path for them to plug into). One generic limit, applied to every
image regardless of what it's a picture of, replaces all five.
"""
import io

#: Formats actually safe to re-encode here. Deliberately narrow: GIF is
#: excluded because Pillow only loads its first frame, and resaving a
#: multi-frame GIF through this path would silently turn an animation
#: into a still image. Anything outside this set is left exactly as
#: uploaded -- unchanged is always safe; a wrong resave is not.
RESIZABLE_FORMATS = {"JPEG", "PNG"}


def shrink_to_max_dimension(content: bytes, max_dimension,
                             mime_type: str = None) -> bytes:
    """Return `content` resized so neither side exceeds max_dimension.

    Never raises -- this runs inside the upload path, and a broken or
    unusual file must not stop someone attaching it. Returns the
    original bytes unchanged for anything this can't safely shrink:
    not an image, already within the limit, no limit configured, or a
    file Pillow cannot open.
    """
    if not content or not (mime_type or "").startswith("image/"):
        return content
    try:
        max_dimension = int(max_dimension)
    except (TypeError, ValueError):
        return content
    if max_dimension <= 0:
        return content

    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as im:
            fmt = im.format
            width, height = im.size
            if fmt not in RESIZABLE_FORMATS or max(width, height) <= max_dimension:
                return content
            im.load()
            if fmt == "JPEG" and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
            buf = io.BytesIO()
            if fmt == "JPEG":
                im.save(buf, fmt, quality=85, optimize=True)
            else:
                im.save(buf, fmt, optimize=True)
            return buf.getvalue()
    except Exception:
        # Corrupt, truncated, or a format Pillow cannot read -- not our
        # problem to diagnose here; the original bytes are stored as-is,
        # same as before this parameter existed.
        return content
