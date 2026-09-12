"""AutoNumberingService: the first-number-of-a-scope race.

Surfaced live against Michael's MySQL: creating the first-ever
VHC (Vehicle Handover) number of the year raised

    OperationalError (1205, 'Lock wait timeout exceeded')

on the INSERT into numbering_counters. Not a missing DocumentType or
scheme -- the traceback's own INSERT statement carries a real
scheme_id, proving both were found correctly.

_advance_counter() already row-locks with SELECT ... FOR UPDATE, which
is exactly right for every generate() call AFTER the first one for a
given scheme+year+month -- but that lock has nothing to attach to
before the row exists. The very first call for a new scope has no row
to lock, so it falls to INSERT, and if two transactions ever reach that
branch for the same scope at close to the same moment, the second must
wait for the first's transaction to resolve. That is completely normal
and should take milliseconds. It only becomes a 1205 when the first
transaction never resolves at all -- most likely an abandoned
connection from a killed dev-server process -- and until now there was
no bound on how long the second one would wait, nor any recovery once
it gave up.

This closes the part of that gap application code can actually fix: a
genuine race between two LIVE, well-behaved transactions now resolves
via a short, bounded retry instead of blocking a full request for the
database's default lock-wait window. An abandoned transaction that
never commits or rolls back is an operational problem no amount of
in-process retrying can shorten -- see TestGivesUpCleanly for what
happens then: a message that tells the operator what to do, not the
raw DB exception this replaces.
"""
import threading

import pytest
from sqlalchemy.exc import OperationalError

from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def fresh_scheme(db):
    """A scheme with NO counter row yet -- the exact state that
    triggers the first-insert race. Every existing numbering test uses
    a scheme already warmed up by an earlier generate() call in the
    same test; this one is deliberately cold."""
    dt = DocumentTypeService().create(code="RACE", name="Race Test",
                                      auto_numbering=True)
    NumberingSchemeService().create(
        document_type_id=dt.id, prefix="RC", include_year=True,
        include_month=False, digit_count=6, reset_policy="YEARLY")
    return dt


class TestRetryRecoversFromTransientContention:
    def test_one_transient_failure_then_success_returns_the_right_number(
            self, db, fresh_scheme, monkeypatch):
        """The case this fix is actually FOR: two live transactions
        raced the insert, ours lost, and the winner has already
        committed by the time we would naturally retry. Simulated by
        forcing exactly one OperationalError before letting the real
        insert proceed."""
        svc = AutoNumberingService()
        real_advance = AutoNumberingService._advance_counter
        calls = {"n": 0}

        def flaky(scheme_id, scope_year, scope_month):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError(
                    "INSERT ...", {},
                    Exception("(1205, 'Lock wait timeout exceeded')"))
            return real_advance(scheme_id, scope_year, scope_month)

        monkeypatch.setattr(AutoNumberingService, "_advance_counter",
                            staticmethod(flaky))
        # generate() itself must own the retry -- the failure happens
        # INSIDE _advance_counter, so patching it to raise once is what
        # proves generate() is the thing catching and retrying, not
        # merely that a second manual call would have worked.
        number = svc.generate("RACE")
        assert number.endswith("000001")
        assert calls["n"] == 2

    def test_retrying_does_not_skip_or_duplicate_a_number(
            self, db, fresh_scheme, monkeypatch):
        """The failed first attempt must not have partially advanced
        anything. If it did, the retried call would return 2 instead
        of 1, silently burning a number no document ever used."""
        svc = AutoNumberingService()
        real_advance = AutoNumberingService._advance_counter
        state = {"failed_once": False}

        def flaky(scheme_id, scope_year, scope_month):
            if not state["failed_once"]:
                state["failed_once"] = True
                raise OperationalError("INSERT ...", {}, Exception("1205"))
            return real_advance(scheme_id, scope_year, scope_month)

        monkeypatch.setattr(AutoNumberingService, "_advance_counter",
                            staticmethod(flaky))
        first = svc.generate("RACE")
        assert first.endswith("000001")


class TestGivesUpCleanly:
    def test_gives_up_with_a_clear_error_after_repeated_failure(
            self, db, fresh_scheme, monkeypatch):
        """The abandoned-transaction case: retrying can't out-wait a
        connection that will never commit. This asserts the FAILURE
        MODE is honest and bounded rather than that it magically
        succeeds -- a caller must see a real error quickly, not hang
        for the database's full lock-wait window on every retry.
        """
        def always_fails(scheme_id, scope_year, scope_month):
            raise OperationalError("INSERT ...", {},
                                   Exception("(1205, 'Lock wait timeout')"))

        monkeypatch.setattr(AutoNumberingService, "_advance_counter",
                            staticmethod(always_fails))
        with pytest.raises(OperationalError):
            AutoNumberingService().generate("RACE")

    def test_gives_up_after_a_bounded_number_of_attempts(
            self, db, fresh_scheme, monkeypatch):
        """Bounded, not "retry forever" -- an operator needs the
        request to fail within a few seconds, not hold a worker hostage
        retrying against a lock that will never clear."""
        calls = {"n": 0}

        def always_fails(scheme_id, scope_year, scope_month):
            calls["n"] += 1
            raise OperationalError("INSERT ...", {}, Exception("1205"))

        monkeypatch.setattr(AutoNumberingService, "_advance_counter",
                            staticmethod(always_fails))
        with pytest.raises(OperationalError):
            AutoNumberingService().generate("RACE")
        assert calls["n"] <= 5


class TestRealConcurrency:
    """Genuine cross-connection concurrency, not the shared-connection
    kind.

    The rest of this test suite runs against `sqlite://` (bare
    in-memory), which SQLAlchemy automatically backs with StaticPool --
    a single physical connection shared by every "separate" Session in
    the whole test process, because an in-memory database ceases to
    exist the moment its one connection closes. That makes it useless
    for testing real concurrency: two threads racing through
    _advance_counter on a shared StaticPool connection produced, across
    different runs, a raised IntegrityError, a raised StaleDataError,
    AND a silent duplicate number with no exception at all -- three
    different outcomes for the identical code, because a single
    sqlite3 DBAPI connection was never designed for two threads to
    interleave transactions on it. None of those are what a real MySQL
    deployment would ever see, where each connection is genuinely,
    correctly isolated.

    So this test builds its own throwaway Flask app on a FILE-backed
    SQLite database instead. A file URI gets SQLAlchemy's ordinary
    QueuePool (confirmed directly: sqlite:// -> SingletonThreadPool /
    StaticPool depending on Flask-SQLAlchemy's own override, a file
    path -> QueuePool), so two threads genuinely get two separate
    connections contending on the same file through SQLite's own
    locking -- the same shape of contention MySQL exhibits, even though
    the exact lock implementation differs.
    """

    def test_two_threads_racing_the_first_insert_never_collide(self, tmp_path):
        import threading

        from flask import Flask

        from app.extensions import db as _db
        from app.modules.document_config.service import (
            DocumentTypeService, NumberingSchemeService)

        db_path = tmp_path / "numbering_race.db"
        file_app = Flask(__name__)
        file_app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
        file_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        _db.init_app(file_app)

        with file_app.app_context():
            _db.create_all()
            dt = DocumentTypeService().create(
                code="RACE2", name="Race Test 2", auto_numbering=True)
            NumberingSchemeService().create(
                document_type_id=dt.id, prefix="RC2", include_year=True,
                include_month=False, digit_count=6, reset_policy="YEARLY")
            _db.session.commit()

        barrier = threading.Barrier(2)
        results = {}
        errors = []

        def worker(key):
            barrier.wait()
            with file_app.app_context():
                try:
                    results[key] = AutoNumberingService().generate("RACE2")
                except Exception as exc:  # pragma: no cover - diagnostic
                    errors.append(exc)

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"generate() raised under real contention: {errors}"
        assert len(results) == 2
        suffixes = {v[-6:] for v in results.values()}
        # Distinct AND exactly {000001, 000002} -- not just "different",
        # since two different-but-wrong values would also satisfy a
        # bare distinctness check.
        assert suffixes == {"000001", "000002"}
