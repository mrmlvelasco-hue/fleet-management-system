"""Reading text off a scanned document.

Preprocessing is BLACK AND WHITE, not greyscale, and that is the single
biggest factor in whether a Certificate of Registration reads at all.

Measured on a real client scan whose CR fields are highlighted in
yellow:

    greyscale             GEELY=no   COOLRAY=no   SUV=yes  2022=yes
    black & white         GEELY=YES  COOLRAY=YES  SUV=yes  2022=yes

Yellow highlighting over black text has poor luminance separation, so
converting to greyscale flattens the contrast and the characters
dissolve. Thresholding to pure black and white recovers them, because
the highlight goes to white and the ink goes to black. The same applies
to the red CR number and the blue LTO stamps that overlay the form.

Several thresholds and scales are tried because no single combination
suits every scan -- a faint photocopy and a crisp digital scan need
different cut points. The readings are parsed separately and voted on
rather than concatenated; see cr_parser.parse_many for why.

The practical advice that follows from this, and which the upload screen
now gives: scan in black and white at 300 DPI.
"""
import io

#: Cut points tried, low to high. A faint scan needs a low threshold; a
#: dark or stamped one needs a high threshold to avoid flooding.
THRESHOLDS = (110, 128, 150)
SCALES = (3, 4, 6)
PSM_MODES = (6, 4)


def read_passes(content: bytes, crop=None):
    """Several independent readings of the same image.

    Returns a list of raw text. Never raises -- an unreadable file
    yields no readings, and the caller falls back to manual entry.
    """
    try:
        from PIL import Image, ImageOps, ImageFilter
        import pytesseract
    except ImportError:
        # OCR is optional: without Tesseract installed the whole feature
        # degrades to manual entry rather than breaking the upload.
        return []

    try:
        im = Image.open(io.BytesIO(content))
        if crop:
            x0, y0, x1, y1 = crop
            im = im.crop((int(im.width * x0), int(im.height * y0),
                         int(im.width * x1), int(im.height * y1)))
        base = im.convert("L")
    except Exception:
        return []

    passes = []
    for scale in SCALES:
        try:
            big = base.resize((base.width * scale, base.height * scale),
                             Image.LANCZOS)
            big = ImageOps.autocontrast(big)
            big = big.filter(ImageFilter.SHARPEN)
        except Exception:
            continue
        for threshold in THRESHOLDS:
            try:
                mono = big.point(lambda v, t=threshold: 0 if v < t else 255)
                for psm in PSM_MODES:
                    passes.append(pytesseract.image_to_string(
                        mono, config=f"--psm {psm}"))
            except Exception:
                continue
    return passes


def ocr_available() -> bool:
    """Whether text extraction can run at all in this deployment."""
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False
