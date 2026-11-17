"""Required documents at vehicle enrollment (2026-11-17).

Requested: "during creation of the Vehicle, CR, OR and Front and Rear
View ... required during enrollment ... add in the system parameter so
that during migration of the vehicle in import ... can be off".

Two System Parameters decide it:

  REQUIRE_VEHICLE_ENROLLMENT_DOCUMENTS   BOOLEAN  the on/off switch
  VEHICLE_ENROLLMENT_REQUIRED_DOCUMENTS  STRING   WHICH documents, as
      attachment document-type codes (Lookup ATTACHMENT_DOC_TYPE)

Kept separate so switching the rule off for a migration never loses the
list, and so the client can change the list (add the insurance policy,
drop a photo) without a code release.

With NO parameter row the rule is OFF: that is the behaviour every
install had before this existed. The seed turns it on -- the client's
decision, recorded in the database where they can see and change it.
"""
import io
import json

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.core.attachments.models import Attachment
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.master_data.org.service import (
    AreaDistrictService, BranchService, OrgCompanyService, RegionService)
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.enrollment_documents import (
    MissingEnrollmentDocumentError, required_documents)
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.system_admin.models import SystemParameter
from app.modules.user_management.models import Permission, Role, User

ALL_FOUR = "CR,OR,PHOTO_FRONT,PHOTO_BACK"


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (120, 140, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _pdf():
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _file(name, content=None, mime=None):
    if content is None:
        content = _pdf() if name.endswith(".pdf") else _png()
    if mime is None:
        mime = "application/pdf" if name.endswith(".pdf") else "image/png"
    return FileStorage(stream=io.BytesIO(content), filename=name,
                       content_type=mime)


def _four():
    return {"CR": _file("cr.pdf"), "OR": _file("or.pdf"),
            "PHOTO_FRONT": _file("front.png"),
            "PHOTO_BACK": _file("rear.png")}


def _params(required=True, documents=ALL_FOUR):
    for code, value, data_type in (
            ("REQUIRE_VEHICLE_ENROLLMENT_DOCUMENTS",
             "true" if required else "false", "BOOLEAN"),
            ("VEHICLE_ENROLLMENT_REQUIRED_DOCUMENTS", documents, "STRING")):
        row = SystemParameter.query.filter_by(code=code).first()
        if row is None:
            _db.session.add(SystemParameter(
                code=code, value=value, data_type=data_type,
                group_name="VEHICLE"))
        else:
            row.value = value
    _db.session.commit()


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    role = Role(name="Vehicle Enroller")
    role.permissions = Permission.query.filter(Permission.code.in_(
        ["vehicle.view", "vehicle.create", "vehicle.update"])).all()
    user = User(username="enroller", email="enroller@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])

    vt = VehicleTypeService().create(code="LV-E", name="Light",
                                     category="LIGHT")
    region = RegionService().create(code="LUZ-E", name="Luzon")
    area = AreaDistrictService().create(code="NCR-E", name="Metro Manila",
                                        region_id=region.id)
    company = OrgCompanyService().create(
        short_name="Excellence", region_id=region.id,
        area_district_id=area.id)
    branch = BranchService().create(code="BR-E", name="Enroll Branch",
                                    company_id=company.id)
    brand = VehicleBrandService().create("Mitsubishi")
    VehicleModelService().create(brand.id, "Triton")
    db.session.commit()
    return {"user": user, "vt": vt, "branch": branch}


def _fields(env, **over):
    fields = {"vehicle_type_id": env["vt"].id, "brand": "Mitsubishi",
              "model": "Triton", "year": 2026,
              "branch_id": env["branch"].id,
              "conduction_number": "E-001", "plate_number": "ENR-1111"}
    fields.update(over)
    return fields


def _create(env, documents=None, **over):
    return VehicleService().create(
        strict=True, documents=documents, user=env["user"],
        **_fields(env, **over))


def _docs_of(vehicle):
    rows = Attachment.query.filter_by(
        reference_table="vehicles", reference_id=vehicle.id).all()
    return {r.document_type: r for r in rows}


# ── The parameters ──────────────────────────────────────────────────────────

def test_with_no_parameter_row_nothing_is_required(env):
    assert required_documents() == []
    vehicle = _create(env)
    assert vehicle.id is not None


def test_switched_on_the_four_documents_are_required(env):
    _params()
    assert [d["code"] for d in required_documents()] == [
        "CR", "OR", "PHOTO_FRONT", "PHOTO_BACK"]


def test_each_requirement_carries_its_lookup_label(env):
    _params()
    labels = {d["code"]: d["label"] for d in required_documents()}
    assert labels["CR"] == "CR — Certificate of Registration"
    assert labels["PHOTO_BACK"] == "Vehicle Photo — Back"


def test_photos_are_marked_image_only_and_scans_are_not(env):
    _params()
    image_only = {d["code"]: d["image_only"] for d in required_documents()}
    assert image_only == {"CR": False, "OR": False,
                          "PHOTO_FRONT": True, "PHOTO_BACK": True}


def test_switched_off_nothing_is_required_but_the_list_is_kept(env):
    _params(required=False)
    assert required_documents() == []
    assert SystemParameter.query.filter_by(
        code="VEHICLE_ENROLLMENT_REQUIRED_DOCUMENTS").first().value == ALL_FOUR


def test_the_list_is_the_clients_to_change(env):
    _params(documents="OR, cr ,INSURANCE")
    assert [d["code"] for d in required_documents()] == [
        "OR", "CR", "INSURANCE"]


def test_a_code_that_is_not_a_document_type_is_ignored(env):
    """A typo in the parameter must not make enrollment impossible --
    there would be no file input that could ever satisfy it."""
    _params(documents="CR,ORR,PHOTO_FRONT")
    assert [d["code"] for d in required_documents()] == ["CR", "PHOTO_FRONT"]


def test_a_code_listed_twice_is_asked_for_once(env):
    _params(documents="CR,OR,CR")
    assert [d["code"] for d in required_documents()] == ["CR", "OR"]


def test_switched_on_with_an_empty_list_requires_nothing(env):
    _params(documents="")
    assert required_documents() == []
    assert _create(env).id is not None


# ── Enrollment ──────────────────────────────────────────────────────────────

def test_enrollment_without_documents_is_refused_and_nothing_is_saved(env):
    _params()
    with pytest.raises(MissingEnrollmentDocumentError) as exc:
        _create(env)
    assert exc.value.missing == ["CR", "OR", "PHOTO_FRONT", "PHOTO_BACK"]
    assert Vehicle.query.count() == 0
    assert Attachment.query.count() == 0


def test_the_refusal_names_what_is_missing(env):
    _params()
    documents = _four()
    del documents["OR"], documents["PHOTO_BACK"]
    with pytest.raises(MissingEnrollmentDocumentError) as exc:
        _create(env, documents=documents)
    assert exc.value.missing == ["OR", "PHOTO_BACK"]
    assert "OR — Official Receipt" in str(exc.value)
    assert "Vehicle Photo — Back" in str(exc.value)
    assert "Certificate of Registration" not in str(exc.value)
    assert Vehicle.query.count() == 0


def test_a_file_input_left_empty_counts_as_missing(env):
    _params()
    documents = _four()
    documents["CR"] = FileStorage(stream=io.BytesIO(b""), filename="")
    with pytest.raises(MissingEnrollmentDocumentError) as exc:
        _create(env, documents=documents)
    assert exc.value.missing == ["CR"]


def test_with_all_four_the_vehicle_is_enrolled_and_each_is_filed_by_type(env):
    _params()
    vehicle = _create(env, documents=_four())
    docs = _docs_of(vehicle)
    assert set(docs) == {"CR", "OR", "PHOTO_FRONT", "PHOTO_BACK"}
    assert docs["CR"].original_filename == "cr.pdf"
    assert docs["PHOTO_BACK"].original_filename == "rear.png"
    assert all(a.uploaded_by == env["user"].id for a in docs.values())
    assert all(a.file_data for a in docs.values())


def test_the_photos_are_the_ones_the_assignment_memo_prints(env):
    """The Vehicle Assignment Memo finds its two photos by these exact
    document types -- enrollment must file them under the same codes."""
    _params()
    vehicle = _create(env, documents=_four())
    for code in ("PHOTO_FRONT", "PHOTO_BACK"):
        assert Attachment.query.filter_by(
            reference_table="vehicles", reference_id=vehicle.id,
            document_type=code, is_active=True).count() == 1


def test_a_photo_that_is_not_an_image_is_refused_before_anything_is_saved(env):
    _params()
    documents = _four()
    documents["PHOTO_FRONT"] = _file("front.pdf")
    with pytest.raises(MissingEnrollmentDocumentError) as exc:
        _create(env, documents=documents)
    assert exc.value.field == "doc_PHOTO_FRONT"
    assert "image" in str(exc.value).lower()
    assert Vehicle.query.count() == 0
    assert Attachment.query.count() == 0


def test_a_file_type_the_system_does_not_accept_is_refused_before_saving(env):
    _params()
    documents = _four()
    documents["OR"] = _file("or.exe", content=b"MZ", mime="application/x-msdownload")
    with pytest.raises(MissingEnrollmentDocumentError) as exc:
        _create(env, documents=documents)
    assert exc.value.field == "doc_OR"
    assert Vehicle.query.count() == 0
    assert Attachment.query.count() == 0


def test_a_scan_may_be_an_image(env):
    _params()
    documents = _four()
    documents["CR"] = _file("cr.jpg", content=_png(), mime="image/jpeg")
    vehicle = _create(env, documents=documents)
    assert _docs_of(vehicle)["CR"].original_filename == "cr.jpg"


def test_documents_supplied_while_the_rule_is_off_are_still_kept(env):
    """Waiving the REQUIREMENT must not disable the feature."""
    _params(required=False)
    vehicle = _create(env, documents={"CR": _file("cr.pdf")})
    assert set(_docs_of(vehicle)) == {"CR"}


def test_an_extra_document_beyond_the_required_list_is_kept_too(env):
    _params(documents="CR")
    vehicle = _create(env, documents={"CR": _file("cr.pdf"),
                                      "OR": _file("or.pdf")})
    assert set(_docs_of(vehicle)) == {"CR", "OR"}


def test_a_duplicate_plate_is_still_reported_as_a_duplicate(env):
    """The document rule must not mask the vehicle's own validation."""
    from app.modules.master_data.vehicle.service import DuplicateVehicleError
    _params()
    _create(env, documents=_four())
    with pytest.raises(DuplicateVehicleError):
        _create(env, documents=_four(), conduction_number="E-002")


def test_editing_a_vehicle_never_asks_for_the_documents_again(env):
    _params(required=False)
    vehicle = _create(env)
    _params(required=True)
    updated = VehicleService().update(vehicle.id, strict=True, color="White")
    assert updated.color == "White"


# ── OR number on the vehicle ────────────────────────────────────────────────

def test_or_number_can_be_recorded_at_enrollment(env):
    vehicle = _create(env, or_number="OR-2026-000123", cr_number="CR-778899")
    assert vehicle.or_number == "OR-2026-000123"
    assert vehicle.cr_number == "CR-778899"


# ── HTTP ────────────────────────────────────────────────────────────────────

def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "enroller", "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def _auth(client):
    return {"Authorization": f"Bearer {_token(client)}"}


def _post_multipart(client, env, files=None, **over):
    data = {"payload": json.dumps(_fields(env, **over))}
    for code, (name, content, mime) in (files or {}).items():
        data[f"doc_{code}"] = (io.BytesIO(content), name, mime)
    r = client.post("/api/v1/vehicles", data=data,
                    content_type="multipart/form-data", headers=_auth(client))
    return r.status_code, json.loads(r.get_data(as_text=True))


def _upload_set():
    return {"CR": ("cr.pdf", _pdf(), "application/pdf"),
            "OR": ("or.pdf", _pdf(), "application/pdf"),
            "PHOTO_FRONT": ("front.png", _png(), "image/png"),
            "PHOTO_BACK": ("rear.png", _png(), "image/png")}


def test_api_tells_the_form_what_to_ask_for(client, env):
    _params()
    r = client.get("/api/v1/vehicles/enrollment-requirements",
                   headers=_auth(client))
    body = json.loads(r.get_data(as_text=True))
    assert r.status_code == 200
    assert body["required"] is True
    assert [d["code"] for d in body["documents"]] == [
        "CR", "OR", "PHOTO_FRONT", "PHOTO_BACK"]
    assert body["documents"][2] == {
        "code": "PHOTO_FRONT", "label": "Vehicle Photo — Front",
        "image_only": True, "field": "doc_PHOTO_FRONT"}


def test_api_says_nothing_is_required_when_switched_off(client, env):
    _params(required=False)
    r = client.get("/api/v1/vehicles/enrollment-requirements",
                   headers=_auth(client))
    body = json.loads(r.get_data(as_text=True))
    assert body == {"required": False, "documents": []}


def test_api_multipart_enrollment_with_all_documents(client, env):
    _params()
    status, body = _post_multipart(client, env, files=_upload_set(),
                                   or_number="OR-555")
    assert status == 201
    assert body["or_number"] == "OR-555"
    vehicle = Vehicle.query.one()
    assert set(_docs_of(vehicle)) == {"CR", "OR", "PHOTO_FRONT", "PHOTO_BACK"}


def test_api_missing_documents_come_back_as_field_errors(client, env):
    _params()
    files = _upload_set()
    del files["PHOTO_BACK"]
    status, body = _post_multipart(client, env, files=files)
    assert status == 400
    assert body["error"] == "validation"
    assert set(body["fields"]) == {"doc_PHOTO_BACK"}
    assert "Vehicle Photo — Back" in body["fields"]["doc_PHOTO_BACK"]
    assert Vehicle.query.count() == 0


def test_api_every_missing_document_gets_its_own_field_error(client, env):
    _params()
    status, body = _post_multipart(client, env)
    assert status == 400
    assert set(body["fields"]) == {"doc_CR", "doc_OR", "doc_PHOTO_FRONT",
                                   "doc_PHOTO_BACK"}


def test_api_json_enrollment_is_refused_while_documents_are_required(
        client, env):
    _params()
    r = client.post("/api/v1/vehicles", json=_fields(env),
                    headers=_auth(client))
    assert r.status_code == 400
    assert Vehicle.query.count() == 0


def test_api_json_enrollment_still_works_when_nothing_is_required(
        client, env):
    r = client.post("/api/v1/vehicles", json=_fields(env),
                    headers=_auth(client))
    assert r.status_code == 201


def test_api_other_validation_still_names_its_own_field(client, env):
    _params()
    status, body = _post_multipart(client, env, files=_upload_set(), year="")
    assert status == 400
    assert "year" in body["fields"]


def test_api_or_number_can_be_edited(client, env):
    vehicle = _create(env)
    r = client.put(f"/api/v1/vehicles/{vehicle.id}",
                   json={"or_number": "OR-999"}, headers=_auth(client))
    assert r.status_code == 200
    assert json.loads(r.get_data(as_text=True))["or_number"] == "OR-999"


# ── Excel import ────────────────────────────────────────────────────────────

def test_import_reports_a_blocker_while_documents_are_required(env):
    from app.modules.master_data.vehicle.import_service import (
        import_blockers)
    _params()
    blockers = import_blockers()
    assert len(blockers) == 1
    assert "REQUIRE_VEHICLE_ENROLLMENT_DOCUMENTS" in blockers[0]


def test_import_has_no_blocker_when_switched_off_or_never_set(env):
    from app.modules.master_data.vehicle.import_service import (
        import_blockers)
    assert import_blockers() == []
    _params(required=False)
    assert import_blockers() == []


def _import_file(env):
    from app.modules.master_data.vehicle.import_service import build_template
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(build_template()))
    ws = wb.active
    header = [c.value for c in ws[1]]
    ws.delete_rows(2, ws.max_row)
    row = {"conduction_number": "IMP-001", "plate_number": "IMP-1111",
           "vehicle_type_code": "LV-E", "branch_code": "BR-E",
           "brand": "Mitsubishi", "model": "Triton", "year": 2026,
           "or_number": "OR-IMPORT-1", "cr_number": "CR-IMPORT-1"}
    ws.append([row.get(h) for h in header])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_preview_carries_the_blocker(env):
    from app.modules.master_data.vehicle.import_service import import_vehicles
    _params()
    stats = import_vehicles(io.BytesIO(_import_file(env)), dry_run=True)
    assert len(stats["blockers"]) == 1


def test_api_import_commit_is_refused_while_blocked(client, env):
    _params()
    r = client.post(
        "/api/v1/vehicles/import",
        data={"file": (io.BytesIO(_import_file(env)), "v.xlsx"),
              "commit": "1", "total_rows": "1"},
        content_type="multipart/form-data", headers=_auth(client))
    assert r.status_code == 400
    assert "REQUIRE_VEHICLE_ENROLLMENT_DOCUMENTS" in json.loads(
        r.get_data(as_text=True))["message"]
    assert Vehicle.query.count() == 0


def test_import_works_once_the_rule_is_switched_off(env):
    from app.modules.master_data.vehicle.import_service import import_vehicles
    _params(required=False)
    stats = import_vehicles(io.BytesIO(_import_file(env)), dry_run=False)
    assert stats["created"] == 1
    assert stats["blockers"] == []
    vehicle = Vehicle.query.one()
    assert vehicle.or_number == "OR-IMPORT-1"
    assert vehicle.cr_number == "CR-IMPORT-1"


def test_the_import_template_has_an_or_number_column(env):
    from app.modules.master_data.vehicle.import_service import build_template
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(build_template()))
    header = [c.value for c in wb.active[1]]
    assert "or_number" in header
    assert header.index("or_number") == header.index("cr_number") + 1


# ── Seed ────────────────────────────────────────────────────────────────────

def test_the_seed_switches_the_rule_on_with_the_four_documents(env):
    from app.cli import _seed_system_parameters
    _seed_system_parameters()
    _db.session.commit()
    switch = SystemParameter.query.filter_by(
        code="REQUIRE_VEHICLE_ENROLLMENT_DOCUMENTS").one()
    documents = SystemParameter.query.filter_by(
        code="VEHICLE_ENROLLMENT_REQUIRED_DOCUMENTS").one()
    assert (switch.value, switch.data_type) == ("true", "BOOLEAN")
    assert documents.value == ALL_FOUR
    assert len(switch.description) <= 255
    assert len(documents.description) <= 255
    assert [d["code"] for d in required_documents()] == [
        "CR", "OR", "PHOTO_FRONT", "PHOTO_BACK"]


def test_reseeding_never_switches_a_rule_back_on(env):
    from app.cli import _seed_system_parameters
    _seed_system_parameters()
    _db.session.commit()
    SystemParameter.query.filter_by(
        code="REQUIRE_VEHICLE_ENROLLMENT_DOCUMENTS").one().value = "false"
    _db.session.commit()
    _seed_system_parameters()
    _db.session.commit()
    assert required_documents() == []
