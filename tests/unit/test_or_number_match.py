"""OR-number OCR match check (2026-11-09 client request).

The client's own words: "can you scan the attached file to check [the OR]
number if same as encoded OR number". The design mirrors the existing CR
extraction's honesty principle (see app/core/extraction/cr_parser.py's
module docstring and the 42% field-exact measurement that motivated
warn-rather-than-trust): OCR on a scan is never assumed perfect, so:

  * a clean match says so
  * a clear mismatch is reported, never silently accepted
  * no extractable text (blank scan, OCR unavailable, non-image file)
    is SKIPPED SILENTLY, never treated as a mismatch -- client decision,
    2026-11-09 AskUserQuestion answer: "Skip silently (recommended)".

Matching is normalized-substring: punctuation, spacing and case in both
the typed number and the scanned text are stripped before comparing, so
"OR-12345" typed against a scan reading "OR 1 2 3 4 5" (a common OCR
character-spacing artifact) still matches.
"""
import pytest

from app.core.extraction.or_number_match import match_or_number


def _mock_passes(monkeypatch, texts):
    monkeypatch.setattr(
        "app.core.extraction.or_number_match.read_passes",
        lambda content, crop=None: texts)


def test_matching_number_is_found_in_scan_text(monkeypatch):
    _mock_passes(monkeypatch, ["OFFICIAL RECEIPT OR NO. 456615250 DATE..."])
    result = match_or_number(b"fake-bytes", "image/png", "456615250")
    assert result.matched is True


def test_mismatched_number_is_reported(monkeypatch):
    _mock_passes(monkeypatch, ["OFFICIAL RECEIPT OR NO. 999999999 DATE..."])
    result = match_or_number(b"fake-bytes", "image/png", "456615250")
    assert result.matched is False
    assert result.note


def test_punctuation_case_and_spacing_differences_are_ignored(monkeypatch):
    _mock_passes(monkeypatch, ["or no. OR-4566-15250"])
    result = match_or_number(b"fake-bytes", "image/png", "456615250")
    assert result.matched is True


def test_no_extractable_text_skips_silently(monkeypatch):
    """A blank/unreadable scan must NEVER be treated as a mismatch --
    the client's explicit decision."""
    _mock_passes(monkeypatch, ["", "   ", ""])
    result = match_or_number(b"fake-bytes", "image/png", "456615250")
    assert result.matched is None


def test_no_passes_at_all_skips_silently(monkeypatch):
    """OCR unavailable (pytesseract/tesseract missing) -> read_passes
    returns [] -- same silent-skip outcome, not a mismatch."""
    _mock_passes(monkeypatch, [])
    result = match_or_number(b"fake-bytes", "image/png", "456615250")
    assert result.matched is None


def test_blank_typed_or_number_skips_silently_without_running_ocr(
        monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.core.extraction.or_number_match.read_passes",
        lambda content, crop=None: calls.append(1) or ["anything"])
    result = match_or_number(b"fake-bytes", "image/png", "")
    assert result.matched is None
    assert not calls, "OCR should never run when there is nothing to check"


@pytest.mark.parametrize("texts", [
    ["OR NO. 456615250 something"],
    ["random noise", "OR NO. 456615250 something else"],
])
def test_a_match_found_in_any_one_pass_is_enough(monkeypatch, texts):
    """Several OCR passes are taken at different scales/thresholds (see
    ocr.py) -- only one needs to find the number."""
    _mock_passes(monkeypatch, texts)
    result = match_or_number(b"fake-bytes", "image/png", "456615250")
    assert result.matched is True
