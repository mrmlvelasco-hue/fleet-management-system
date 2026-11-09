"""Check the typed OR number against what OCR reads off the OR scan.

Client request (2026-11-09, verbatim): "can you scan the attached file
to check number if same as encoded OR number". This is deliberately a
much smaller check than the Certificate of Registration extraction in
cr_parser.py: the Official Receipt isn't parsed into structured fields
at all, because there is nothing else on it worth extracting -- the
only question that matters is whether the one printed number is the
one that was typed.

Same honesty principle as the rest of app/core/extraction/ (see
cr_parser.py's module docstring): OCR on a scanned government form is
never assumed perfect, so a reading with no usable text is reported as
"nothing to check", never as a mismatch. The client was explicit about
this on 2026-11-09: an unreadable or blank scan must be skipped
silently, not flagged -- it is the same lesson as the CR number's "OR
number is printed in red / small and smudges" problem, not a reason to
accuse the user of typing the wrong number.
"""
import re
from dataclasses import dataclass

from app.core.extraction.ocr import read_passes


@dataclass
class ORMatchResult:
    #: True = found in at least one OCR pass. False = text was read but
    #: the number wasn't found in any pass. None = nothing to check
    #: (blank input, unreadable scan, or OCR unavailable) -- never
    #: treated as a mismatch.
    matched: bool | None
    note: str = ""


def _normalize(value):
    """Upper-cased, letters/digits only.

    OCR routinely inserts or drops spaces, hyphens and periods around a
    printed number ("OR-12345" read as "OR 1 2 3 4 5"), and the typed
    value may use different punctuation than the printed one. Comparing
    only the alphanumeric content is what survives both.
    """
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()


def match_or_number(content: bytes, mime_type, typed_or_number) -> ORMatchResult:
    """Does any OCR reading of `content` contain `typed_or_number`?

    Never raises. Returns matched=None (skip silently) whenever there is
    nothing trustworthy to compare against: a blank typed number, no
    extractable text at all, or OCR being unavailable in this
    environment (read_passes() already returns [] in that case).
    """
    needle = _normalize(typed_or_number)
    if not needle:
        return ORMatchResult(matched=None)

    passes = read_passes(content) or []
    usable_passes = [p for p in passes if p and p.strip()]
    if not usable_passes:
        return ORMatchResult(
            matched=None,
            note="The scan could not be read, so the OR number was not "
                 "checked against it.")

    for text in usable_passes:
        if needle in _normalize(text):
            return ORMatchResult(matched=True)

    return ORMatchResult(
        matched=False,
        note=f"The OR number entered ({typed_or_number}) was not found "
             f"on the uploaded OR scan. Please double-check it against "
             f"the document.")
