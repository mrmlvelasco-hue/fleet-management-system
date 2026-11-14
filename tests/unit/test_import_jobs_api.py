"""POST .../import?commit=1 goes async: returns {job_id} immediately
(202), and GET /api/v1/import-jobs/<id> is polled for progress/result.
Covers the shared contract once against the vendor importer (the
simplest of the three); driver/vehicle wiring is identical and is
exercised by their own import test files staying green.
"""
import json
import time
from io import BytesIO

from openpyxl import Workbook

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.vendor.import_service import TEMPLATE_COLUMNS
from app.modules.master_data.org.service import BranchService
from app.modules.user_management.models import Permission, Role, User


def _sheet(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Vendors"
    headers = [c for c, _req, _note in TEMPLATE_COLUMNS]
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def env(db):
    sync_permissions()
    BranchService().create(code="BR-JOB", name="Job Branch")
    role = Role(name="role-jobimporter")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vendor.view", "vendor.create"])).all()
    u = User(username="jobimporter", email="jobimporter@e.com",
            password_hash=hash_password("secret123"), is_active=True)
    u.roles = [role]
    db.session.add_all([role, u])
    db.session.commit()


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "jobimporter", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


GOOD = {"code": "VEN-JOB1", "name": "Job Vendor One"}


def test_commit_returns_202_with_a_job_id(client, db):
    env(db)
    buf = _sheet([GOOD])
    r = client.post("/api/v1/vendors/import", headers=_hdr(client),
                    data={"file": (buf, "vendors.xlsx"), "commit": "1",
                          "total_rows": "1"},
                    content_type="multipart/form-data")
    assert r.status_code == 202
    body = json.loads(r.get_data(as_text=True))
    assert isinstance(body["job_id"], int)


def test_polling_the_job_reaches_done_with_final_counts(client, db):
    env(db)
    buf = _sheet([GOOD])
    r = client.post("/api/v1/vendors/import", headers=_hdr(client),
                    data={"file": (buf, "vendors.xlsx"), "commit": "1",
                          "total_rows": "1"},
                    content_type="multipart/form-data")
    job_id = json.loads(r.get_data(as_text=True))["job_id"]

    status = None
    for _ in range(50):
        r = client.get(f"/api/v1/import-jobs/{job_id}", headers=_hdr(client))
        status = json.loads(r.get_data(as_text=True))
        if status["status"] == "done":
            break
        time.sleep(0.05)

    assert status["status"] == "done"
    assert status["created_count"] == 1
    assert status["skipped_count"] == 0
    assert status["total_rows"] == 1


def test_dry_run_is_unchanged_still_synchronous(client, db):
    env(db)
    buf = _sheet([{"code": "VEN-DRYRUN", "name": "Dry Run Vendor"}])
    r = client.post("/api/v1/vendors/import", headers=_hdr(client),
                    data={"file": (buf, "vendors.xlsx")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    # dry_run still reports what WOULD be created (that's what the
    # Review screen's "Ready to import" count comes from) -- the thing
    # that must stay unchanged is that nothing is actually persisted.
    assert body["dry_run"] is True
    assert body["created"] == 1
    from app.modules.master_data.vendor.models import Vendor
    assert Vendor.query.filter_by(code="VEN-DRYRUN").first() is None


def test_a_different_user_cannot_poll_someone_elses_job(client, db):
    env(db)
    role = Role(name="role-otherimporter")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vendor.view", "vendor.create"])).all()
    u = User(username="otherimporter", email="other@e.com",
            password_hash=hash_password("secret123"), is_active=True)
    u.roles = [role]
    db.session.add_all([role, u])
    db.session.commit()

    buf = _sheet([GOOD])
    r = client.post("/api/v1/vendors/import", headers=_hdr(client),
                    data={"file": (buf, "vendors.xlsx"), "commit": "1",
                          "total_rows": "1"},
                    content_type="multipart/form-data")
    job_id = json.loads(r.get_data(as_text=True))["job_id"]

    other_tok = json.loads(client.post(
        "/api/v1/auth/token",
        json={"username": "otherimporter", "password": "secret123"}
    ).get_data(as_text=True))["access_token"]
    r2 = client.get(f"/api/v1/import-jobs/{job_id}",
                    headers={"Authorization": f"Bearer {other_tok}"})
    assert r2.status_code == 404
