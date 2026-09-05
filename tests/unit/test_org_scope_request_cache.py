"""UserOrgScopeService.list_for_user is memoised per REQUEST.

covers() calls it on every invocation, and covers() is called once per
row when filtering a list by org scope. Measured on the dashboard at
5,000 vehicles: 6,589 identical queries returning the same handful of
rows -- 99% of every query the dashboard issued, and about two seconds
of the 7.9 it took.

The cache is scoped to flask.g, so it lives exactly as long as one
request. That boundary is the whole design: longer and an administrator
changing someone's scope would not take effect until a restart, which
is a security problem rather than a performance one.
"""
import pytest
from sqlalchemy import event

from app.extensions import db
from app.modules.master_data.org.models import Branch
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(app):
    b1 = Branch(code="A", name="Alpha")
    b2 = Branch(code="B", name="Beta")
    db.session.add_all([b1, b2])
    u = User(username="u", email="u@e.com", password_hash="x", is_active=True)
    db.session.add(u)
    db.session.commit()
    return u, b1, b2


def _count_queries(fn):
    n = {"c": 0}

    def _listen(conn, cur, stmt, params, ctx, many):
        if "user_org_scopes" in stmt:
            n["c"] += 1

    event.listen(db.engine, "before_cursor_execute", _listen)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _listen)
    return n["c"]


def test_repeated_covers_calls_query_once_per_request(app, env):
    """The fix. 500 rows filtered by scope used to mean 500 queries."""
    u, b1, _b2 = env
    svc = UserOrgScopeService()
    svc.assign(user_id=u.id, scope_type="BRANCH", branch_id=b1.id)

    with app.test_request_context("/"):
        n = _count_queries(
            lambda: [svc.covers(u.id, branch_id=b1.id) for _ in range(50)])

    assert n == 1


def test_the_answer_is_still_correct(app, env):
    """Caching must not turn a scope check into a rubber stamp."""
    u, b1, b2 = env
    svc = UserOrgScopeService()
    svc.assign(user_id=u.id, scope_type="BRANCH", branch_id=b1.id)

    with app.test_request_context("/"):
        assert svc.covers(u.id, branch_id=b1.id) is True
        assert svc.covers(u.id, branch_id=b2.id) is False
        # Repeated, to prove the cached path agrees with the first.
        assert svc.covers(u.id, branch_id=b2.id) is False


def test_granting_a_scope_mid_request_takes_effect(app, env):
    """A request that CHANGES scopes and then re-reads them must not see
    the answer it cached moments earlier."""
    u, b1, b2 = env
    svc = UserOrgScopeService()
    svc.assign(user_id=u.id, scope_type="BRANCH", branch_id=b1.id)

    with app.test_request_context("/"):
        assert svc.covers(u.id, branch_id=b2.id) is False
        svc.assign(user_id=u.id, scope_type="BRANCH", branch_id=b2.id)
        assert svc.covers(u.id, branch_id=b2.id) is True


def test_the_cache_does_not_leak_between_requests(app, env):
    """The reason it is scoped to flask.g. A longer-lived cache would
    mean an admin revoking a scope did not take effect until a restart
    -- a security problem, not a performance one."""
    u, b1, b2 = env
    svc = UserOrgScopeService()
    svc.assign(user_id=u.id, scope_type="BRANCH", branch_id=b1.id)

    with app.test_request_context("/"):
        assert svc.covers(u.id, branch_id=b2.id) is False

    # A separate request, after the scope is granted outside it.
    svc.assign(user_id=u.id, scope_type="BRANCH", branch_id=b2.id)
    with app.test_request_context("/"):
        assert svc.covers(u.id, branch_id=b2.id) is True


def test_users_do_not_share_a_cache_slot(app, env):
    """Keyed by user_id. Sharing one slot would hand one user another's
    scopes."""
    u, b1, b2 = env
    other = User(username="other", email="o@e.com", password_hash="x",
                 is_active=True)
    db.session.add(other)
    db.session.commit()
    svc = UserOrgScopeService()
    svc.assign(user_id=u.id, scope_type="BRANCH", branch_id=b1.id)
    svc.assign(user_id=other.id, scope_type="BRANCH", branch_id=b2.id)

    with app.test_request_context("/"):
        assert svc.covers(u.id, branch_id=b1.id) is True
        assert svc.covers(other.id, branch_id=b1.id) is False


def test_include_inactive_is_part_of_the_key(app, env):
    """The two answers differ. Sharing a slot would let a maintenance
    screen asking for inactive rows poison the security check that must
    not see them."""
    u, b1, _b2 = env
    svc = UserOrgScopeService()
    scope = svc.assign(user_id=u.id, scope_type="BRANCH", branch_id=b1.id)

    with app.test_request_context("/"):
        assert len(svc.list_for_user(u.id)) == 1
        svc.remove(scope.id)
        assert len(svc.list_for_user(u.id)) == 0
        assert len(svc.list_for_user(u.id, include_inactive=True)) == 1


def test_it_works_outside_a_request_context(app, env):
    """CLI and Celery have no flask.g; the memo must fall through
    rather than explode."""
    u, b1, _b2 = env
    svc = UserOrgScopeService()
    svc.assign(user_id=u.id, scope_type="BRANCH", branch_id=b1.id)
    assert svc.covers(u.id, branch_id=b1.id) is True
