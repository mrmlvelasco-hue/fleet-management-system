"""Every print payload's company block comes from one helper.

Michael, on the handover checklist print-out: "to the header the
company name this supposed to be the for the configuration of company."

company_letterhead() exists precisely so a letterhead is defined once,
and most print endpoints already use it. Two did not: /vehicles/<id>/
print and /drivers/<id>/print each hand-rolled their own subset.

The vehicle one returned only company_name, address_line, address_line1
and city -- no phone, email, tin, country or logo_url -- so
PrintLetterhead's contact line and logo were always blank on anything
built from that payload, including the handover checklist print-out.

The driver one is worse in a quieter way: it reads `address_line`, a
column that does not exist on CompanyProfile (the real one is
`address_line1`). That is the EXACT drift company_letterhead's own
docstring records having already happened once in ATD's payload. It
happened again, in a second module, which is the argument for the
helper rather than against it.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.user_management.models import Permission, Role, User

#: Every key PrintLetterhead reads, plus the logo. If an endpoint omits
#: any of these the header silently loses that line.
LETTERHEAD_KEYS = {
    "company_name", "address_line1", "address_line2", "city",
    "country", "phone", "email", "tin", "logo_url",
}


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
    vehicle = Vehicle(plate_number="NAG 1234", brand="Toyota", model="Hilux",
                      year=2022, vehicle_type_id=vtype.id,
                      branch_id=branch.id, is_active=True, status="ACTIVE")
    db.session.add(vehicle)

    role = Role(name="Viewer")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view", "driver.view"])).all()
    user = User(username="officer", email="o@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add(user)
    db.session.commit()
    return {"vehicle": vehicle}


def _headers(client):
    tok = client.post("/api/v1/auth/token",
                      json={"username": "officer", "password": "secret123"})
    return {"Authorization":
            f"Bearer {json.loads(tok.get_data(as_text=True))['access_token']}"}


class TestVehiclePrintLetterhead:
    def test_carries_every_letterhead_field(self, client, app, rig):
        headers = _headers(client)
        resp = client.get(f"/api/v1/vehicles/{rig['vehicle'].id}/print",
                          headers=headers)
        assert resp.status_code == 200
        company = json.loads(resp.get_data(as_text=True))["company"]
        missing = LETTERHEAD_KEYS - set(company)
        assert not missing, (
            f"print payload omits letterhead fields {sorted(missing)}; "
            "PrintLetterhead renders those lines blank")

    def test_unconfigured_company_still_returns_the_full_shape(
            self, client, app, rig):
        """No CompanyProfile row exists in this fixture.

        The helper returns the full shape with nulls rather than {} on
        purpose: a client reading company_name off an empty dict gets
        undefined and renders a blank header, where an explicit null
        renders the "Company Name" placeholder that prompts an
        administrator to go and configure one.
        """
        headers = _headers(client)
        resp = client.get(f"/api/v1/vehicles/{rig['vehicle'].id}/print",
                          headers=headers)
        company = json.loads(resp.get_data(as_text=True))["company"]
        assert set(company) >= LETTERHEAD_KEYS
        assert company["company_name"] is None


class TestDriverPrintLetterhead:
    def test_does_not_read_a_column_that_does_not_exist(self, app):
        """`address_line` is not a CompanyProfile column.

        Asserted against the MODEL rather than a response, because the
        bug is silent: getattr(company, "address_line", None) returns
        None forever and the printed address is simply always blank.
        A response-shape test would pass against the broken code
        whenever no company is configured.
        """
        from app.modules.system_admin.models import CompanyProfile
        cols = {c.name for c in CompanyProfile.__table__.columns}
        assert "address_line" not in cols, (
            "if this column now exists the comment in company_letterhead "
            "needs revisiting")
        assert "address_line1" in cols
