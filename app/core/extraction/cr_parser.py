"""Turn the text read off a Certificate of Registration into fields.

Label-anchored rather than positional: the LTO has changed the CR layout
more than once (compare a 2009 form with a 2022 one), but the field
LABELS have stayed remarkably stable -- "CHASSIS NO.", "ENGINE NO.",
"YEAR MODEL". Anchoring on labels survives a layout change; anchoring on
coordinates does not.

Every field comes back with a confidence state, because the honest
answer to "is this right?" varies enormously by field:

  HIGH   -- validated (a VIN that passes its check digit), or a value
            constrained to a known set (FUEL is GAS/DIESEL/ELECTRIC).
  MEDIUM -- read cleanly but unverifiable (a chassis number that isn't
            a 17-character VIN; a make the system hasn't seen before).
  LOW    -- read, but failed a check it should have passed.

Anything below HIGH is marked needs_review, and the service refuses to
save it until a person confirms they have checked it.
"""
import re
from dataclasses import dataclass, field as _field

#: FUEL is one of a tiny known set, so a reading outside it is wrong
#: rather than merely unverified.
KNOWN_FUELS = {"GAS", "GASOLINE", "DIESEL", "ELECTRIC", "HYBRID", "LPG",
               "CNG"}


@dataclass
class ExtractedField:
    value: str
    confidence: str = "MEDIUM"
    note: str = ""
    #: What the scanner actually read, when validation changed it.
    raw: str = None
    needs_review: bool = _field(default=True)


def _clean(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip(" .:-\u2014")
    return value or None


def _find(text, *labels, pattern=r"([A-Z0-9\-\. ]+)"):
    """First match for any of the given labels.

    Labels are matched loosely -- OCR routinely drops the full stop in
    "NO." or splits "YEAR MODEL" across a line break.
    """
    for label in labels:
        loose = label.replace(" ", r"[\s\.]*").replace(".", r"\.?")
        m = re.search(loose + r"[\s\.:]*" + pattern, text, re.I)
        if m:
            cleaned = _clean(m.group(1))
            if cleaned:
                return cleaned
    return None


def parse_many(texts):
    """Parse several OCR readings of the SAME document and vote.

    A scan is normally run through Tesseract more than once -- different
    scales and page-segmentation modes suit different parts of a form.
    Concatenating those readings into one blob and matching against it
    is a mistake I made and measured: labels then appear many times over
    and a regex happily matches text produced by the worst pass, so
    "MAKE GEELY" became "BODY".

    Parsing each reading separately and voting fixes that, and gives a
    real confidence signal for free: a value several independent passes
    agree on is far more trustworthy than one only a single pass saw.
    """
    from collections import Counter
    per_field = {}
    for text in texts:
        for name, item in parse_cr(text).items():
            per_field.setdefault(name, []).append(item)

    out = {}
    for name, items in per_field.items():
        counts = Counter(i.value for i in items)
        best, votes = counts.most_common(1)[0]
        winner = next(i for i in items if i.value == best)
        agreement = votes / len(items)

        # Agreement can only ever LOWER confidence here, never raise it.
        #
        # I tried promoting well-agreed fields to HIGH and measured the
        # result on a real scan: every pass agreed that the make was
        # "BODY" -- because the PARSER was matching the wrong text, not
        # because the OCR was unstable -- and the field was promoted to
        # HIGH with review cleared. A confidently wrong value written
        # without review is precisely the failure this whole feature
        # exists to prevent.
        #
        # Agreement measures OCR STABILITY. It says nothing about
        # whether the parser grabbed the right text. Only an independent
        # check can do that: a VIN check digit, a fuel type from a known
        # set, a year in a plausible range. So HIGH is reserved for
        # fields that pass such a check, and everything else stays
        # reviewable no matter how many passes concur.
        if votes >= 2 and agreement >= 0.6 and winner.confidence == "MEDIUM":
            winner.note = (
                (winner.note + " " if winner.note else "")
                + f"{votes} readings of the scan agreed on this value, "
                  f"though that only means the scan read consistently — "
                  f"it still needs checking against the document.")
        elif votes == 1 and len(items) > 2:
            winner.confidence = "LOW"
            winner.needs_review = True
            winner.note = ((winner.note + " " if winner.note else "")
                          + "Only one reading of the scan found this, and "
                            "the others disagreed — please check it.")
        out[name] = winner
    return out


def high_confidence_only(fields):
    """Drop everything the system cannot vouch for.

    The client's rule, and the right one: offer only fields that pass an
    independent check, and let people type the rest. A half-right
    suggestion in a form field is worse than an empty one -- it invites
    a glance and a nod, where an empty field invites reading the
    document.

    HIGH means a real check passed, not "the scan looked clean":
      * chassis  -- 17-character VIN whose check digit verifies
      * fuel     -- a value from the known set
      * year     -- four digits in a plausible range

    Everything else is returned separately so the UI can say what it
    saw but could not confirm, without pre-filling anything.
    """
    trusted = {k: v for k, v in fields.items() if not v.needs_review}
    unverified = {k: v for k, v in fields.items() if v.needs_review}
    return trusted, unverified


def parse_cr(text):
    """Fields keyed by Vehicle column name, so applying them is direct."""
    if not text:
        return {}
    out = {}
    upper = str(text).upper()

    # ── Chassis: the one field with a real checksum behind it ───────
    chassis = _find(upper, "CHASSIS NO", "CHASIS NO",
                   pattern=r"([A-Z0-9]{8,20})")
    if chassis:
        from app.core.extraction.vin import validate_vin
        vin = validate_vin(chassis)
        if vin.is_valid:
            conf, review = "HIGH", False
        elif vin.is_unverifiable:
            conf, review = "MEDIUM", True
        else:
            conf, review = "LOW", True
        out["chassis_number"] = ExtractedField(
            value=vin.value, confidence=conf, note=vin.note,
            raw=vin.corrected_from, needs_review=review)

    # ── Engine: no checksum exists, so never better than MEDIUM ─────
    engine = _find(upper, "ENGINE NO", pattern=r"([A-Z0-9]{6,25})")
    if engine:
        out["engine_number"] = ExtractedField(
            value=engine, confidence="MEDIUM", needs_review=True,
            note=("Engine numbers have no checksum, so this cannot be "
                  "verified automatically. Please check it against the "
                  "document."))

    plate = _find(upper, "PLATE NO", pattern=r"([A-Z]{2,3}[\s\-]?\d{3,4})")
    if plate:
        out["plate_number"] = ExtractedField(
            value=plate.replace(" ", "").replace("-", ""),
            confidence="MEDIUM", needs_review=True)

    mv_file = _find(upper, "MV FILE NO", pattern=r"([0-9]{4}\-[0-9]{8,14})")
    if mv_file:
        out["mv_file_number"] = ExtractedField(
            value=mv_file, confidence="MEDIUM", needs_review=True)

    cr_no = _find(upper, "CR NO", pattern=r"([0-9]{6,12}(?:\-[0-9])?)")
    if cr_no:
        out["cr_number"] = ExtractedField(
            value=cr_no, confidence="MEDIUM", needs_review=True,
            note=("The CR number is printed in red, which scans poorly \u2014 "
                  "worth a close look."))

    make = _find(upper, "MAKE", pattern=r"([A-Z][A-Z\-]{2,20})")
    if make:
        out["brand"] = ExtractedField(value=make.title() if make.isalpha()
                                     else make,
                                     confidence="MEDIUM", needs_review=True)
        out["brand"].value = make

    series = _find(upper, "SERIES", pattern=r"([A-Z0-9\-\. ]{2,30})")
    if series:
        out["model"] = ExtractedField(value=series, confidence="MEDIUM",
                                     needs_review=True)

    body = _find(upper, "BODY TYPE", pattern=r"([A-Z\-]{3,20})")
    if body:
        out["vehicle_body_type"] = ExtractedField(
            value=body, confidence="MEDIUM", needs_review=True)

    year = _find(upper, "YEAR MODEL", pattern=r"((?:19|20)\d{2})")
    if year:
        out["year"] = ExtractedField(value=year, confidence="HIGH",
                                    needs_review=False,
                                    note="A four-digit year in a plausible "
                                         "range.")

    fuel = _find(upper, "FUEL", pattern=r"([A-Z]{3,10})")
    if fuel:
        known = fuel.upper() in KNOWN_FUELS
        out["fuel_type"] = ExtractedField(
            value=fuel.upper(), confidence="HIGH" if known else "LOW",
            needs_review=not known,
            note="" if known else
                 f"{fuel!r} is not a fuel type this system recognises.")

    disp = _find(upper, "PISTON DISPLACEMENT", "DISPLACEMENT",
                pattern=r"([0-9]{3,5})")
    if disp:
        out["displacement"] = ExtractedField(value=disp, confidence="MEDIUM",
                                            needs_review=True)

    return out
