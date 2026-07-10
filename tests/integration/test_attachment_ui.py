import io
from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="AttachRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="nora", email="n@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "nora", "password": "pw123456"})
    return u


def test_csrf_meta_tag_present_on_dashboard(client, db):
    """Regression: JS read a non-existent csrf_token cookie; must use meta tag."""
    _login(client, db)
    resp = client.get("/")
    assert b'name="csrf-token"' in resp.data


def test_attachment_upload_response_includes_view_and_download_urls(client, db, tmp_path):
    _login(client, db, codes=["vehicle.view", "vehicle.create",
                              "attachment.upload"])
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService

    vt = VehicleTypeService().create(code="LV2", name="Light", category="LIGHT")
    branch = BranchService().create(code="BR3", name="Branch 3")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Ranger", year=2024,
        branch_id=branch.id, conduction_number="XYZ-999")

    data = {
        "reference_table": "vehicles",
        "reference_id": str(vehicle.id),
        "file": (io.BytesIO(b"fake-image-bytes"), "photo.png"),
    }
    resp = client.post("/master/attachments/upload", data=data,
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["is_image"] is True
    assert "view_url" in payload
    assert "download_url" in payload


def test_attachment_view_endpoint_serves_inline(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create",
                              "attachment.upload"])
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService

    vt = VehicleTypeService().create(code="LV3", name="Light2", category="LIGHT")
    branch = BranchService().create(code="BR4", name="Branch 4")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Dmax", year=2024,
        branch_id=branch.id, conduction_number="DEF-111")

    data = {
        "reference_table": "vehicles",
        "reference_id": str(vehicle.id),
        "file": (io.BytesIO(b"fake-image-bytes-2"), "pic.jpg"),
    }
    upload_resp = client.post("/master/attachments/upload", data=data,
                              content_type="multipart/form-data")
    att_id = upload_resp.get_json()["id"]

    view_resp = client.get(f"/master/attachments/{att_id}/view")
    assert view_resp.status_code == 200
    # Inline view must not force a download (no attachment disposition)
    disposition = view_resp.headers.get("Content-Disposition", "")
    assert "attachment" not in disposition.lower()
