"""Is this scan good enough to read text from?

Measured, not guessed. Three real Certificate of Registration scans from
the previous system were run through Tesseract at several scales and
page-segmentation modes, best result kept, and scored against ground
truth read off the documents by eye:

    14 of 33 fields exact -- 42%

Plate number and CR number came through on all three (large print on the
Official Receipt half). Engine number and chassis number failed on all
three. NZO-629's chassis `NCP929018510` read as `NUP SZ TO 1U`, and its
registration date `10/01/2009` read as `0/01/2008` -- a plausible-looking
wrong year, which is more dangerous than obvious garbage because nobody
questions it.

The cause was resolution, not tuning. Those files are 984x555 px for a
two-document spread: roughly 12 inches of paper across 984 pixels, about
82 DPI, where Tesseract wants 300. Upscaling cannot recover detail that
was never captured.

So the honest thing is to measure the scan and say so at upload time,
while the person still has the document in hand and can rescan -- rather
than let them trust an extraction that quietly invented a chassis number.
"""
import io

#: Below this, text extraction is unreliable enough to be worth warning
#: about. Deliberately well under the 300 DPI target: the point is to
#: catch the 82 DPI thumbnails, not to nag about a decent 250 DPI scan.
LOW_DPI_THRESHOLD = 200

#: Short edge of A4 in inches. Used to estimate DPI from pixel width,
#: since scans rarely carry accurate DPI metadata -- and when they do it
#: is often the scanner's nominal setting rather than what was captured.
A4_SHORT_EDGE_INCHES = 8.27


def assess_scan(content: bytes, mime_type: str = None) -> dict:
    """Estimate the working resolution of an uploaded image.

    Never raises. This runs inside the upload path, and a broken or
    unusual file must not stop someone attaching it -- a document that
    cannot be assessed is simply not warned about.

    Returns estimated_dpi None for anything that is not a readable
    image (PDFs, spreadsheets, corrupt bytes).
    """
    result = {"estimated_dpi": None, "is_low_resolution": False,
              "width": None, "height": None, "message": None}

    if not content or not (mime_type or "").startswith("image/"):
        return result

    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as im:
            width, height = im.size
    except Exception:
        # Corrupt, truncated, or a format Pillow cannot read. Not our
        # problem to diagnose here; the upload proceeds unwarned.
        return result

    result["width"], result["height"] = width, height

    # Estimate from the SHORT edge, so the reading is the same whether
    # the page was scanned portrait or landscape.
    short_edge = min(width, height)
    dpi = int(round(short_edge / A4_SHORT_EDGE_INCHES))
    result["estimated_dpi"] = dpi

    if dpi < LOW_DPI_THRESHOLD:
        result["is_low_resolution"] = True
        result["message"] = (
            f"This scan is about {dpi} DPI ({width}x{height}). Text "
            f"extraction needs around 300 DPI to read engine and chassis "
            f"numbers reliably \u2014 at this resolution it typically "
            f"recovers under half the fields, and can misread the ones "
            f"it does find. The file is saved and viewable; if you plan "
            f"to extract details from it, rescan at 300 DPI in BLACK AND "
            f"WHITE, with the whole document flat in the frame. Black and "
            f"white matters more than it sounds: highlighter and coloured "
            f"stamps wash out in greyscale and take the text under them "
            f"with it."
        )
    return result
