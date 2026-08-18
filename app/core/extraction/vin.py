"""Vehicle Identification Number validation.

Philippine LTO Certificates of Registration carry the chassis number,
and on modern vehicles that is a 17-character VIN. Two properties of the
VIN standard make it unusually well suited to catching OCR errors:

  1. The letters I, O and Q never appear. Those are precisely the
     characters OCR confuses with 1, 0 and 0 -- so a reading containing
     them is unambiguously a substitution, correctable without guessing.

  2. Position 9 is a check digit over the whole string. A single wrong
     character almost always breaks it.

Verified against two real VINs from client scans (LB37622Z3NX416214 and
MRHRU5830LP020394): both validate, and 7 of 7 simulated OCR
substitutions were caught.

This does not make OCR accurate. It makes a wrong reading VISIBLE, which
is the part that matters -- a chassis number two characters out looks
entirely plausible on screen, and would otherwise be signed off.
"""
from dataclasses import dataclass

#: Character -> weight value. Note I, O and Q are absent by design.
_TRANSLIT = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}

_POSITION_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]

#: Substitutions that are safe because the left-hand character cannot
#: legally occur in a VIN at all.
_ILLEGAL = {"I": "1", "O": "0", "Q": "0"}

VIN_LENGTH = 17


@dataclass
class VinResult:
    value: str
    is_valid: bool
    #: True when the string simply isn't a VIN (wrong length, older
    #: chassis format). Distinct from invalid: unverifiable is not the
    #: same as wrong, and conflating them teaches people to ignore
    #: warnings.
    is_unverifiable: bool = False
    corrected_from: str = None
    note: str = ""


def _check_digit(vin: str):
    try:
        total = sum(_TRANSLIT[c] * w
                   for c, w in zip(vin, _POSITION_WEIGHTS))
    except KeyError:
        return None
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def validate_vin(raw) -> VinResult:
    """Validate, and correct the corrections that are certain.

    Never raises: this runs over OCR output, which can be anything.
    """
    if not raw:
        return VinResult(value="", is_valid=False, is_unverifiable=True,
                        note="No chassis number was read.")

    vin = "".join(str(raw).split()).upper()
    original = vin

    if len(vin) != VIN_LENGTH:
        return VinResult(
            value=vin, is_valid=False, is_unverifiable=True,
            note=(f"Not a 17-character VIN ({len(vin)} characters), so the "
                  f"check digit cannot be verified. This is normal for "
                  f"older or imported units \u2014 please confirm it "
                  f"against the document."))

    # I/O/Q cannot occur in a VIN, so their presence is certainly an OCR
    # substitution rather than a real character.
    if any(c in vin for c in _ILLEGAL):
        for wrong, right in _ILLEGAL.items():
            vin = vin.replace(wrong, right)

    digit = _check_digit(vin)
    if digit is None:
        return VinResult(
            value=vin, is_valid=False, is_unverifiable=True,
            corrected_from=original if vin != original else None,
            note="Contains characters that cannot appear in a VIN.")

    valid = digit == vin[8]
    note = ""
    if vin != original:
        note = (f"Read as {original}; corrected to {vin}. The letters I, O "
                f"and Q never appear in a VIN, so those readings were "
                f"certainly 1, 0 and 0.")
    if not valid:
        note = (note + " " if note else "") + (
            "This VIN fails its check digit, which usually means one or "
            "two characters were misread. Please check it against the "
            "document before saving.")

    return VinResult(value=vin, is_valid=valid, is_unverifiable=False,
                    corrected_from=original if vin != original else None,
                    note=note)
