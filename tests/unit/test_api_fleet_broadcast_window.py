"""The Fleet Broadcast inbox date window.

Symptom: the bell announced "Fuel Price Advisory" while the inbox showed
no broadcasts at all.

`publish()` fans notifications out to the whole audience with no date
check. `list_my_fleet_broadcasts` then applied a window the notifications
never saw, and evaluated it against `datetime.utcnow()`.

Nothing else in the app works in UTC. `date.today()` -- server local,
naive -- is the convention at 45 call sites across maintenance, dashboard
and reports; `datetime.utcnow()` appeared exactly three times, two of them
these broadcast endpoints. And the value being compared is naive: a
`datetime-local` picker sends `2026-09-19T00:00:00` with no offset, so
what is stored is the admin's wall clock. Comparing a Manila wall clock
against UTC hid a broadcast for the first eight hours of its effective
day and expired it eight hours early.

So the fix is to compare local against local, not to teach this one
endpoint about time zones -- a UTC concept here and naive local
everywhere else is what produced the bug.

The clock is pinned, with local and UTC eight hours apart. Under a UTC
machine the two are identical and the bug cannot reproduce at all, which
is exactly why it survived a suite running in a UTC container.
"""
import json
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.core.security.password import hash_password
from app.modules.api import admin_ops
from app.modules.system_admin.services.fleet_broadcast_service import (
    FleetBroadcastService)
from app.modules.user_management.models import User


#: A fixed Manila wall clock. The window has to be pinned rather than left
#: to the machine's TZ, because under UTC the two clocks are identical and
#: the bug cannot reproduce at all -- which is exactly why it survived a
#: suite that runs in a UTC container. Pinning here instead of setting TZ:
#: time.tzset() does not exist on Windows, and the dev machines are Windows.
MANILA_NOW = datetime(2026, 9, 19, 7, 0, 0)
UTC_OFFSET = timedelta(hours=8)


class _PinnedClock(datetime):
    """datetime with both clocks pinned 8 hours apart.

    Subclassed rather than stubbed so fromisoformat/strptime and the rest
    keep working for the other callers in admin_ops.
    """

    @classmethod
    def now(cls, tz=None):
        return MANILA_NOW

    @classmethod
    def utcnow(cls):
        return MANILA_NOW - UTC_OFFSET


@pytest.fixture(autouse=True)
def manila_clock(monkeypatch):
    monkeypatch.setattr(admin_ops, "datetime", _PinnedClock)


@pytest.fixture()
def env(app):
    admin = User(username="bcadm", email="bcadm@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
    driver = User(username="bcdrv", email="bcdrv@e.com",
                  password_hash=hash_password("secret123"), is_active=True,
                  mobile_access=True)
    db.session.add_all([admin, driver])
    db.session.commit()
    return {"admin": admin, "driver": driver}


def _publish(admin, no, **dates):
    svc = FleetBroadcastService()
    row = svc.create(
        user=admin, broadcast_no=no, broadcast_type="ADVISORY",
        category="FUEL", title=f"Advisory {no}",
        message="Diesel up P1.50/L.", priority="HIGH",
        recipients=[{"recipient_type": "ALL_USERS"}], **dates)
    svc.publish(row.id, user=admin)
    return row


def _inbox(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "bcdrv", "password": "secret123"})
    token = json.loads(r.get_data(as_text=True)).get("access_token")
    r = client.get("/api/v1/my/fleet-broadcasts",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return json.loads(r.get_data(as_text=True))["items"]


def test_a_broadcast_already_in_effect_reaches_the_inbox(client, env):
    """An hour into its effective window, on the admin's clock. This is
    the notice whose bell alert arrived while the list stayed empty."""
    _publish(env["admin"], "FB-2026-0010",
             effective_date=MANILA_NOW - timedelta(hours=1))

    assert [b["broadcast_no"] for b in _inbox(client)] == ["FB-2026-0010"]


def test_a_broadcast_not_yet_in_effect_is_withheld(client, env):
    _publish(env["admin"], "FB-2026-0011",
             effective_date=MANILA_NOW + timedelta(hours=1))

    assert _inbox(client) == []


def test_an_expired_broadcast_is_withheld(client, env):
    """Expiry is the same comparison in the other direction: an hour past
    its expiry on the admin's clock, this must be gone -- under the UTC
    comparison it lingered for another eight hours."""
    _publish(env["admin"], "FB-2026-0012",
             effective_date=MANILA_NOW - timedelta(days=2),
             expiry_date=MANILA_NOW - timedelta(hours=1))

    assert _inbox(client) == []


def test_a_broadcast_with_no_dates_always_reaches_the_inbox(client, env):
    _publish(env["admin"], "FB-2026-0013")

    assert [b["broadcast_no"] for b in _inbox(client)] == ["FB-2026-0013"]