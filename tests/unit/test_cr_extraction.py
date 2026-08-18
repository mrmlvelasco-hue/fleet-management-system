"""Reading a Certificate of Registration, and knowing when not to trust it.

OCR on a CR cannot be made perfect. Measured on real scans it reaches
roughly 82% of fields, and the failures land on exactly the values that
matter most and are hardest to eyeball: engine and chassis numbers,
where a two-character slip produces something that looks entirely
plausible.

So the design goal is not accuracy, it is HONESTY: every extracted value
carries a confidence state, anything that fails validation is flagged,
and a flagged field cannot be saved without a person explicitly
confirming they have checked it against the paper.

The strongest tool here is that Philippine chassis numbers are VINs.
A VIN is 17 characters, never contains I, O or Q, and carries a check
digit in position 9 -- so the very substitutions OCR makes (O for 0,
I for 1, B for 8) are detectable rather than silent. Verified against
two real VINs from client scans: 7 of 7 simulated OCR errors caught.

Note on test data: the real scans are deliberately NOT committed as
fixtures. They carry the owner's home address and a third party's name,
and that does not belong in version control. The logic is tested against
representative extracted text; the image path is verified in a browser.
"""
import pytest


# ── VIN validation ──────────────────────────────────────────────────

def test_a_real_vin_validates():
    from app.core.extraction.vin import validate_vin
    # From a client CR; check digit verified by hand.
    result = validate_vin("MRHRU5830LP020394")
    assert result.is_valid
    assert result.value == "MRHRU5830LP020394"


def test_a_second_real_vin_validates():
    from app.core.extraction.vin import validate_vin
    assert validate_vin("LB37622Z3NX416214").is_valid


@pytest.mark.parametrize("bad, what", [
    ("NRHRU5830LP020394", "N read for M"),
    ("MRHRU5B30LP020394", "B read for 8"),
    ("MRHRU5830LP020395", "final digit misread"),
    ("MRHRU5930LP020394", "9 read for 8 mid-string"),
])
def test_ambiguous_substitutions_are_caught_by_the_check_digit(bad, what):
    """The whole point: a wrong chassis number must not pass silently.

    These are substitutions between characters that BOTH legally occur
    in a VIN, so there is no way to know which was meant -- the check
    digit is what catches them.
    """
    from app.core.extraction.vin import validate_vin
    assert not validate_vin(bad).is_valid, f"{what} slipped through"


@pytest.mark.parametrize("bad, what", [
    ("MRHRU583OLP020394", "O read for 0"),
    ("MRHRU5830LPO20394", "O read for 0, later in the string"),
    ("MRHRU583OLPO20394", "two illegal-letter substitutions at once"),
])
def test_illegal_letter_substitutions_are_corrected_not_merely_rejected(
        bad, what):
    """I, O and Q cannot occur in a VIN, so these readings are
    unambiguously wrong AND unambiguously fixable. Correcting beats
    flagging: it saves the person retyping seventeen characters, and the
    change is reported rather than hidden.
    """
    from app.core.extraction.vin import validate_vin
    result = validate_vin(bad)
    assert result.corrected_from == bad, f"{what} was not corrected"
    assert result.is_valid, f"{what} did not validate after correction"
    assert result.value == "MRHRU5830LP020394"
    assert result.note, "the correction was made silently"


def test_an_ambiguous_illegal_letter_still_ends_up_flagged():
    """I is corrected to 1 by convention, but it can equally be a
    misread L -- as it was in this real VIN, where the true character
    was L. The correction is applied, the check digit then fails, and
    the field is flagged rather than saved wrong. Exactly the outcome
    wanted: the system does not pretend to know which was meant.
    """
    from app.core.extraction.vin import validate_vin
    result = validate_vin("MRHRU5830IP020394")   # true value has L
    assert result.corrected_from == "MRHRU5830IP020394"
    assert not result.is_valid
    assert "check digit" in result.note


def test_illegal_letters_are_auto_corrected_with_certainty():
    """I, O and Q cannot legally appear in a VIN, so a reading
    containing them is unambiguously an OCR substitution -- correctable
    without guessing."""
    from app.core.extraction.vin import validate_vin
    result = validate_vin("MRHRU583OLP02O394")
    assert result.corrected_from is not None
    assert "O" not in result.value
    assert result.value == "MRHRU5830LP020394"
    assert result.is_valid


def test_a_correction_is_reported_not_hidden():
    """Someone must be able to see that the system changed what the
    scanner read."""
    from app.core.extraction.vin import validate_vin
    result = validate_vin("MRHRU583OLP02O394")
    assert result.corrected_from == "MRHRU583OLP02O394"
    assert result.note


def test_wrong_length_is_rejected_without_pretending():
    from app.core.extraction.vin import validate_vin
    result = validate_vin("MRHRU5830LP")
    assert not result.is_valid
    assert "17" in result.note


def test_empty_input_does_not_raise():
    from app.core.extraction.vin import validate_vin
    assert not validate_vin("").is_valid
    assert not validate_vin(None).is_valid


def test_non_vin_chassis_numbers_are_not_condemned():
    """Older or imported units may carry a chassis number that is not a
    17-character VIN. That is unverifiable, not wrong -- it must be
    reported as UNVERIFIED rather than INVALID, or the operator learns
    to ignore the warnings."""
    from app.core.extraction.vin import validate_vin
    result = validate_vin("NCP929018510")
    assert not result.is_valid
    assert result.is_unverifiable


# ── Parsing a CR ────────────────────────────────────────────────────

SAMPLE_CR_TEXT = """
Republic of the Philippines
DEPARTMENT OF TRANSPORTATION
LAND TRANSPORTATION OFFICE
CERTIFICATE OF REGISTRATION  CR No. 456615250  DATE: 08/08/2022
MV FILE NO. 0401-00001579737   PLATE NO. DAR4260
ENGINE NO. JLH3G15TDN4BA5604690   CHASSIS NO. LB37622Z3NX416214
DENOMINATION SPORTS UTILITY VEHICLE  PISTON DISPLACEMENT 1500
NO. OF CYLINDERS 3   FUEL GAS
MAKE GEELY  SERIES COOLRAY SPORT S  BODY TYPE SUV  YEAR MODEL 2022
GROSS WT. 1340  NET WT. 670  SHIPPING WT. 670  NET CAPACITY 670
"""


def _parse(text=SAMPLE_CR_TEXT):
    from app.core.extraction.cr_parser import parse_cr
    return parse_cr(text)


def test_parser_finds_the_identifiers():
    f = _parse()
    assert f["chassis_number"].value == "LB37622Z3NX416214"
    assert f["engine_number"].value == "JLH3G15TDN4BA5604690"
    assert f["plate_number"].value == "DAR4260"


def test_parser_finds_the_descriptive_fields():
    f = _parse()
    assert f["brand"].value == "GEELY"
    assert f["year"].value == "2022"
    assert f["fuel_type"].value == "GAS"
    assert f["vehicle_body_type"].value == "SUV"


def test_parser_finds_the_registration_numbers():
    f = _parse()
    assert f["cr_number"].value == "456615250"
    assert f["mv_file_number"].value == "0401-00001579737"


def test_a_validated_vin_is_marked_high_confidence():
    f = _parse()
    assert f["chassis_number"].confidence == "HIGH"
    assert f["chassis_number"].needs_review is False


def test_a_failing_vin_is_flagged_for_review():
    """This is the case the client hit: a chassis number two characters
    out, which looks completely plausible on screen."""
    text = SAMPLE_CR_TEXT.replace("LB37622Z3NX416214", "LB37622Z3NX416215")
    f = _parse(text)
    assert f["chassis_number"].needs_review is True
    assert f["chassis_number"].confidence in ("LOW", "MEDIUM")
    assert f["chassis_number"].value, "the reading is still offered"


def test_a_flagged_field_still_shows_its_value():
    """Withholding it entirely would throw away a reading that is 15 of
    17 characters correct -- the person can fix two characters far
    faster than typing the lot."""
    text = SAMPLE_CR_TEXT.replace("LB37622Z3NX416214", "LB37622Z3NX416215")
    assert _parse(text)["chassis_number"].value == "LB37622Z3NX416215"


def test_missing_fields_are_absent_rather_than_blank():
    """A CR with no plate (a brand-new unit awaiting plates) must not
    produce an empty-string plate that overwrites a real one."""
    text = SAMPLE_CR_TEXT.replace("PLATE NO. DAR4260", "PLATE NO.")
    f = _parse(text)
    assert "plate_number" not in f or not f["plate_number"].value


def test_parser_survives_garbage():
    from app.core.extraction.cr_parser import parse_cr
    assert parse_cr("") == {}
    assert parse_cr("\x00\x01 nonsense ###") is not None


def test_every_field_maps_to_a_real_vehicle_column(app):
    """A parser field that does not correspond to a Vehicle attribute
    would silently never be applied."""
    from app.modules.master_data.vehicle.models import Vehicle
    for name in _parse():
        assert hasattr(Vehicle, name), f"Vehicle has no column {name!r}"


# ── The save gate ───────────────────────────────────────────────────

def test_flagged_fields_block_an_unconfirmed_apply(app, db):
    from app.core.extraction.extraction_service import (
        ExtractionService, UnconfirmedFieldsError)
    text = SAMPLE_CR_TEXT.replace("LB37622Z3NX416214", "LB37622Z3NX416215")
    from app.core.extraction.cr_parser import parse_cr
    fields = parse_cr(text)
    with pytest.raises(UnconfirmedFieldsError):
        ExtractionService().apply(vehicle=None, fields=fields,
                                 selected=["chassis_number"],
                                 confirmed=[])


def test_confirming_a_flagged_field_permits_the_apply(app, db):
    """The rule the client asked for: it must be CHECKED before it can
    be saved -- not blocked outright, since they may well have the paper
    in front of them and know the reading is right."""
    from app.core.extraction.extraction_service import ExtractionService
    from app.core.extraction.cr_parser import parse_cr
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="VT-EX", name="SUV",
                                     category="LIGHT")
    br = BranchService().create(code="BR-EX", name="Carmona")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, branch_id=br.id, brand="Geely",
        model="Coolray", year=2022, plate_number="DAR4260",
        conduction_number="CN-EX1")
    text = SAMPLE_CR_TEXT.replace("LB37622Z3NX416214", "LB37622Z3NX416215")
    fields = parse_cr(text)
    applied = ExtractionService().apply(
        vehicle=vehicle, fields=fields, selected=["chassis_number"],
        confirmed=["chassis_number"])
    assert "chassis_number" in applied
    assert vehicle.chassis_number == "LB37622Z3NX416215"


def test_only_selected_fields_are_written(app, db):
    """Extraction proposes; the person disposes. Nothing is written that
    was not explicitly ticked."""
    from app.core.extraction.extraction_service import ExtractionService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="VT-EX2", name="SUV",
                                     category="LIGHT")
    br = BranchService().create(code="BR-EX2", name="Naic")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, branch_id=br.id, brand="Original",
        model="Model", year=2020, plate_number="OLD-1111",
        conduction_number="CN-EX2")
    fields = _parse()
    ExtractionService().apply(vehicle=vehicle, fields=fields,
                             selected=["chassis_number"], confirmed=[])
    assert vehicle.chassis_number == "LB37622Z3NX416214"
    # brand was extracted as GEELY but never ticked
    assert vehicle.brand == "Original"


# ── Only offer what can be vouched for ──────────────────────────────

def test_only_verified_fields_are_offered_for_applying():
    """The client's rule: fill in what the system can prove, and let
    people type the rest. A half-right suggestion sitting in a form
    field is worse than an empty one -- it invites a glance and a nod,
    where an empty field invites reading the document."""
    from app.core.extraction.cr_parser import parse_cr, high_confidence_only
    trusted, unverified = high_confidence_only(parse_cr(SAMPLE_CR_TEXT))
    assert "chassis_number" in trusted      # VIN check digit passed
    assert "engine_number" in unverified    # no checksum exists for it
    assert all(not f.needs_review for f in trusted.values())


def test_a_failed_vin_is_never_offered_as_trusted():
    from app.core.extraction.cr_parser import parse_cr, high_confidence_only
    text = SAMPLE_CR_TEXT.replace("LB37622Z3NX416214", "LB37622Z3NX416215")
    trusted, unverified = high_confidence_only(parse_cr(text))
    assert "chassis_number" not in trusted
    assert "chassis_number" in unverified


def test_unverified_values_are_still_reported(app, db):
    """Shown for reference, not pre-filled -- someone comparing against
    the paper can still use them."""
    from app.core.extraction.cr_parser import parse_cr
    from app.core.extraction.extraction_service import ExtractionService
    result = ExtractionService().propose(parse_cr(SAMPLE_CR_TEXT))
    assert result["unverified"]
    assert result["message"]


def test_a_scan_that_yields_nothing_says_so_plainly(app, db):
    """Extraction must never become a precondition for entering a
    vehicle. If it reads nothing, the form works exactly as before."""
    from app.core.extraction.extraction_service import ExtractionService
    result = ExtractionService().propose({})
    assert result["trusted"] == {}
    assert "manually" in result["message"]


def test_the_message_never_overstates_what_was_verified(app, db):
    from app.core.extraction.cr_parser import parse_cr
    from app.core.extraction.extraction_service import ExtractionService
    text = SAMPLE_CR_TEXT.replace("LB37622Z3NX416214", "LB37622Z3NX416215")
    result = ExtractionService().propose(parse_cr(text))
    assert not result["trusted"] or "verified" in result["message"]
    if not result["trusted"]:
        assert "nothing has been filled in" in result["message"]
