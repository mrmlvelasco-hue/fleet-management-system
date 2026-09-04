"""APK release management.

The tests that matter are the two refusals: publishing must NOT raise
the forced-update floor, and the floor must never exceed the release
that carries it. Both failures lock working phones out of a working
app, and neither shows up until somebody in a yard cannot sign in.
"""
import hashlib
import io
import json

import pytest
from werkzeug.datastructures import FileStorage

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.system_admin.mobile_release_models import (
    MobileAppRelease, MobileAppReleaseFile)
from app.modules.system_admin.mobile_release_service import (
    MobileReleaseError, MobileReleaseService)
from app.modules.user_management.models import Permission, Role, User

APK = b"PK\x03\x04 pretend android package"


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             mobile_access=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def admin(app):
    return _user("relmanager",
                 ["mobileapp.view", "mobileapp.create", "mobileapp.update"])


@pytest.fixture()
def driver(app):
    return _user("juan", ["vehicle.view"])


def _file(name="fms.apk", data=APK):
    return FileStorage(stream=io.BytesIO(data), filename=name)


def _hdr(client, username="relmanager"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def _upload(code=100, name="1.0.0"):
    return MobileReleaseService().upload(_file(), name, code)


# ── upload validation ────────────────────────────────────────────────

def test_upload_creates_a_draft(app):
    r = _upload()
    assert r.status == "DRAFT"
    # Uploading is not releasing. A build can be put in place and
    # checked before any phone is told it exists.
    assert r.is_current is False


def test_upload_records_the_checksum(app):
    r = _upload()
    assert r.checksum_sha256 == hashlib.sha256(APK).hexdigest()


def test_non_apk_is_refused(app):
    with pytest.raises(MobileReleaseError):
        MobileReleaseService().upload(_file("notes.pdf"), "1.0.0", 100)


def test_empty_file_is_refused(app):
    with pytest.raises(MobileReleaseError):
        MobileReleaseService().upload(_file(data=b""), "1.0.0", 100)


def test_version_code_must_increase(app):
    _upload(code=100)
    with pytest.raises(MobileReleaseError):
        _upload(code=100, name="1.0.1")
    with pytest.raises(MobileReleaseError):
        _upload(code=99, name="0.9.9")


def test_version_code_must_beat_archived_builds_too(app):
    """Not merely the current one. A code reused from an archived build
    makes two rows indistinguishable to a phone, which compares codes
    and nothing else."""
    svc = MobileReleaseService()
    first = _upload(code=100)
    svc.publish(first.id)
    second = _upload(code=101, name="1.0.1")
    svc.publish(second.id)  # archives the first
    with pytest.raises(MobileReleaseError):
        _upload(code=100, name="rewind")


# ── publishing ───────────────────────────────────────────────────────

def test_publish_makes_it_current(app):
    svc = MobileReleaseService()
    r = _upload()
    svc.publish(r.id)
    assert svc.current().id == r.id


def test_publishing_leaves_exactly_one_current(app):
    svc = MobileReleaseService()
    a = _upload(code=100)
    svc.publish(a.id)
    b = _upload(code=101, name="1.0.1")
    svc.publish(b.id)

    currents = MobileAppRelease.query.filter_by(is_current=True).all()
    assert len(currents) == 1
    assert currents[0].id == b.id
    assert db.session.get(MobileAppRelease, a.id).status == "ARCHIVED"


def test_publish_does_NOT_raise_the_forced_update_floor(app):
    """THE guard.

    Publishing tells phones a newer build exists. Blocking the old one
    is a different decision with a different blast radius. Coupling them
    would mean every routine release risked locking out whoever had not
    updated yet -- and they would find out in a yard, unable to sign
    in."""
    svc = MobileReleaseService()
    r = _upload(code=100)
    svc.publish(r.id)
    assert db.session.get(
        MobileAppRelease, r.id).min_supported_version_code is None


def test_set_minimum_is_the_deliberate_action(app):
    svc = MobileReleaseService()
    r = _upload(code=100)
    svc.publish(r.id)
    svc.set_minimum(r.id, 100)
    assert db.session.get(
        MobileAppRelease, r.id).min_supported_version_code == 100


def test_minimum_above_the_release_itself_is_refused(app):
    """Would lock out every device INCLUDING those on the newest build,
    leaving nobody with a working app and no way to reach a fix."""
    svc = MobileReleaseService()
    r = _upload(code=100)
    svc.publish(r.id)
    with pytest.raises(MobileReleaseError):
        svc.set_minimum(r.id, 101)


def test_minimum_can_be_cleared(app):
    svc = MobileReleaseService()
    r = _upload(code=100)
    svc.publish(r.id)
    svc.set_minimum(r.id, 100)
    svc.set_minimum(r.id, None)
    assert db.session.get(
        MobileAppRelease, r.id).min_supported_version_code is None


# ── the version endpoint ─────────────────────────────────────────────

def test_version_returns_nulls_when_nothing_published(app, client, driver):
    """Nulls, not a 404. A client forced to tell "no release configured"
    from "endpoint missing" by status code ends up guessing."""
    body = _body(client.get("/api/v1/mobile/version",
                            headers=_hdr(client, "juan")))
    assert body["version_code"] is None
    assert body["download_url"] is None


def test_version_is_readable_by_any_signed_in_user(app, client, driver):
    """No permission code. Every user of the app must be able to find
    out whether they are behind; gating it would mean the people most
    likely to be on an old build cannot discover it."""
    svc = MobileReleaseService()
    r = _upload(code=140, name="1.4.0")
    svc.publish(r.id)

    body = _body(client.get("/api/v1/mobile/version",
                            headers=_hdr(client, "juan")))

    assert body["version_code"] == 140
    assert body["version_name"] == "1.4.0"
    assert body["download_url"].endswith(f"/mobile/releases/{r.id}/download")


def test_version_refuses_an_anonymous_caller(app, client):
    assert client.get("/api/v1/mobile/version").status_code == 401


# ── download ─────────────────────────────────────────────────────────

def test_download_returns_the_exact_bytes(app, client, driver):
    svc = MobileReleaseService()
    r = _upload()
    svc.publish(r.id)

    res = client.get(f"/api/v1/mobile/releases/{r.id}/download",
                     headers=_hdr(client, "juan"))

    assert res.status_code == 200
    assert res.get_data() == APK
    assert hashlib.sha256(res.get_data()).hexdigest() == r.checksum_sha256


def test_download_refuses_an_anonymous_caller(app, client):
    r = _upload()
    assert client.get(
        f"/api/v1/mobile/releases/{r.id}/download").status_code == 401


# ── admin endpoints ──────────────────────────────────────────────────

def test_upload_endpoint_requires_the_permission(app, client, driver):
    res = client.post("/api/v1/mobile/releases",
                      headers=_hdr(client, "juan"),
                      data={"version_name": "1.0.0", "version_code": "100",
                            "file": (io.BytesIO(APK), "fms.apk")},
                      content_type="multipart/form-data")
    assert res.status_code == 403


def test_upload_endpoint_works_for_a_release_manager(app, client, admin):
    res = client.post("/api/v1/mobile/releases", headers=_hdr(client),
                      data={"version_name": "1.0.0", "version_code": "100",
                            "file": (io.BytesIO(APK), "fms.apk")},
                      content_type="multipart/form-data")
    assert res.status_code == 201
    assert _body(res)["status"] == "DRAFT"


def test_a_bad_version_code_reads_as_a_sentence(app, client, admin):
    _upload(code=200)
    res = client.post("/api/v1/mobile/releases", headers=_hdr(client),
                      data={"version_name": "1.0.0", "version_code": "100",
                            "file": (io.BytesIO(APK), "fms.apk")},
                      content_type="multipart/form-data")
    assert res.status_code == 400
    assert "not higher" in _body(res)["message"]


def test_listing_does_not_load_the_binaries(app, client, admin):
    """The reason the tables are split. If the blob rode along on the
    metadata query, a list of ten releases would pull ~200 MB."""
    _upload(code=100)
    rows = MobileReleaseService().list()
    assert len(rows) == 1
    assert not hasattr(rows[0], "file_data")
    assert MobileAppReleaseFile.query.count() == 1


# ── retention: only the current build's binary is kept ───────────────

def test_publishing_purges_the_previous_binary(app):
    """Client decision, 2026-09-03: only the latest APK is stored.

    The BINARY goes; the metadata row stays. That split is deliberate --
    the row is a few hundred bytes and answers "which version was live
    on the 14th, who published it, what changed", and it keeps the
    version code CLAIMED so nobody can re-upload 140 and produce two
    builds a phone cannot tell apart.
    """
    svc = MobileReleaseService()
    old = _upload(code=100)
    svc.publish(old.id)
    assert svc.bytes_for(old.id) == APK

    new = _upload(code=101, name="1.0.1")
    svc.publish(new.id)

    assert svc.bytes_for(old.id) is None
    assert svc.bytes_for(new.id) == APK


def test_the_purged_release_keeps_its_history(app):
    svc = MobileReleaseService()
    old = _upload(code=100)
    svc.publish(old.id)
    new = _upload(code=101, name="1.0.1")
    svc.publish(new.id)

    row = db.session.get(MobileAppRelease, old.id)
    assert row is not None
    assert row.version_code == 100
    assert row.status == "ARCHIVED"
    assert row.checksum_sha256 == hashlib.sha256(APK).hexdigest()
    assert row.file_purged is True


def test_a_purged_version_code_still_cannot_be_reused(app):
    """The reason the metadata row survives. Two builds sharing a
    version code are indistinguishable to a phone, which compares codes
    and nothing else."""
    svc = MobileReleaseService()
    old = _upload(code=100)
    svc.publish(old.id)
    new = _upload(code=101, name="1.0.1")
    svc.publish(new.id)

    with pytest.raises(MobileReleaseError):
        _upload(code=100, name="rewind")


def test_only_one_binary_is_ever_stored(app):
    svc = MobileReleaseService()
    for code in (100, 101, 102, 103):
        r = _upload(code=code, name=f"1.0.{code}")
        svc.publish(r.id)

    assert MobileAppReleaseFile.query.count() == 1


def test_downloading_a_purged_release_reads_as_a_sentence(app, client,
                                                           driver):
    """A device pointed at an old download URL must be told plainly,
    not handed a stack trace or an empty file that installs and
    crashes."""
    svc = MobileReleaseService()
    old = _upload(code=100)
    svc.publish(old.id)
    new = _upload(code=101, name="1.0.1")
    svc.publish(new.id)

    res = client.get(f"/api/v1/mobile/releases/{old.id}/download",
                     headers=_hdr(client, "juan"))

    assert res.status_code == 404
    assert "no longer" in _body(res)["message"].lower()


def test_draft_binaries_are_not_purged_by_publishing_another(app):
    """A draft is a build somebody is still preparing. Publishing an
    unrelated release must not delete the file they just uploaded."""
    svc = MobileReleaseService()
    live = _upload(code=100)
    svc.publish(live.id)
    draft = _upload(code=101, name="1.0.1-rc")

    newer = _upload(code=102, name="1.0.2")
    svc.publish(newer.id)

    assert svc.bytes_for(draft.id) == APK


def test_upload_still_works_on_sqlite_where_there_is_no_packet_limit(app):
    """The packet probe must not block uploads on a backend that has no
    such setting -- and must not fail the whole upload if the probe
    itself errors."""
    r = _upload(code=500, name="5.0.0")
    assert r.status == "DRAFT"
    assert MobileReleaseService().bytes_for(r.id) == APK
