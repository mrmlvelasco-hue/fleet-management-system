"""Vehicle Body Type diagram on the Handover Checklist (2026-11-02).

Michael, on the Handover Checklist: "the picture is in the xlsx has a
every sheet with different picture please check and apply to the
handover checklist" -- i.e. the diagram that prints on the handover
checklist (VehicleChecklistReport.diagram_url, consumed by
VehicleHandoverChecklistPrint.tsx) must depend on the SELECTED
vehicle's body type, not be one fixed image for every vehicle.

/vehicles/<id>/print is the one endpoint both the vehicle profile
print and the handover checklist print already share for every other
field (see test_print_letterhead_shared.py), so the body-type-specific
diagram is resolved there too, from the vehicle's own
`vehicle_body_type` value against the matching
Lookup(lookup_type="VEHICLE_BODY_TYPE") row's picture -- see
test_lookup_picture.py for how a picture gets onto that row.
"""
import io
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.system_admin.models import Lookup
from app.modules.user_management.models import Permission, Role, User

PNG = b"\x89PNG\r\n\x1a\n sedan diagram bytes"


@pytest.fixture()
def rig(db, app):
    sync_permissions()
    db.session.commit()
    branch = Branch(name="HQ", code="HQ")
    db.session.add(branch)
    db.session.flush()
    vtype = VehicleType(code="CARL", name="Car Light", category="CAR")
    db.session.add(vtype)
    db.session.flush()

    sedan = Vehicle(plate_number="NAG 1234", brand="Toyota", model="Vios",
                    year=2022, vehicle_type_id=vtype.id, branch_id=branch.id,
                    is_active=True, status="ACTIVE", vehicle_body_type="SEDAN")
    suv = Vehicle(plate_number="NAG 5678", brand="Toyota", model="Fortuner",
                 year=2022, vehicle_type_id=vtype.id, branch_id=branch.id,
                 is_active=True, status="ACTIVE", vehicle_body_type="SUV")
    no_type = Vehicle(plate_number="NAG 9999", brand="Toyota", model="Hilux",
                      year=2022, vehicle_type_id=vtype.id, branch_id=branch.id,
                      is_active=True, status="ACTIVE", vehicle_body_type=None)
    db.session.add_all([sedan, suv, no_type])

    sedan_lookup = Lookup(lookup_type="VEHICLE_BODY_TYPE", code="SEDAN",
                          description="Sedan", sort_order=1)
    suv_lookup = Lookup(lookup_type="VEHICLE_BODY_TYPE", code="SUV",
                        description="SUV", sort_order=2)
    db.session.add_all([sedan_lookup, suv_lookup])
    db.session.flush()

    role = Role(name="Viewer")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view"])).all()
    user = User(username="officer", email="o@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add(user)
    db.session.commit()
    return {"sedan": sedan, "suv": suv, "no_type": no_type,
           "sedan_lookup": sedan_lookup, "suv_lookup": suv_lookup}


def _headers(client):
    tok = client.post("/api/v1/auth/token",
                      json={"username": "officer", "password": "secret123"})
    return {"Authorization":
            f"Bearer {json.loads(tok.get_data(as_text=True))['access_token']}"}


def _give_picture(lookup_row, data=PNG, name="diagram.png"):
    from werkzeug.datastructures import FileStorage
    from app.core.attachments.attachment_service import AttachmentService
    att = AttachmentService().upload(
        FileStorage(stream=io.BytesIO(data), filename=name),
        "lookups", lookup_row.id)
    lookup_row.picture_attachment_id = att.id
    lookup_row.picture_filename = att.original_filename
    db.session.commit()
    return att


class TestHandoverChecklistBodyTypeDiagram:
    def test_no_diagram_url_when_body_type_has_no_picture(self, client, app,
                                                           rig):
        resp = client.get(f"/api/v1/vehicles/{rig['sedan'].id}/print",
                          headers=_headers(client))
        assert json.loads(resp.get_data(as_text=True))["diagram_url"] is None

    def test_no_diagram_url_when_vehicle_has_no_body_type(self, client, app,
                                                          rig):
        _give_picture(rig["sedan_lookup"])
        resp = client.get(f"/api/v1/vehicles/{rig['no_type'].id}/print",
                          headers=_headers(client))
        assert json.loads(resp.get_data(as_text=True))["diagram_url"] is None

    def test_diagram_url_resolves_to_the_vehicles_own_body_type(self, client,
                                                                app, rig):
        sedan_att = _give_picture(rig["sedan_lookup"], name="sedan.png")
        suv_att = _give_picture(rig["suv_lookup"], name="suv.png")

        sedan_resp = json.loads(
            client.get(f"/api/v1/vehicles/{rig['sedan'].id}/print",
                      headers=_headers(client)).get_data(as_text=True))
        suv_resp = json.loads(
            client.get(f"/api/v1/vehicles/{rig['suv'].id}/print",
                      headers=_headers(client)).get_data(as_text=True))

        assert sedan_resp["diagram_url"] == (
            f"/api/v1/lookups/{rig['sedan_lookup'].id}/picture"
            f"?v={sedan_att.id}")
        assert suv_resp["diagram_url"] == (
            f"/api/v1/lookups/{rig['suv_lookup'].id}/picture?v={suv_att.id}")
        # Different vehicles, different body types -> different diagrams.
        assert sedan_resp["diagram_url"] != suv_resp["diagram_url"]

    def test_the_resolved_diagram_actually_serves(self, client, app, rig):
        """End to end: the URL this endpoint hands back is one the
        checklist's <img> tag can actually fetch."""
        _give_picture(rig["sedan_lookup"], data=PNG)
        resp = json.loads(
            client.get(f"/api/v1/vehicles/{rig['sedan'].id}/print",
                      headers=_headers(client)).get_data(as_text=True))
        url = resp["diagram_url"]
        img = client.get(url, headers=_headers(client))
        assert img.status_code == 200
        assert img.get_data() == PNG
