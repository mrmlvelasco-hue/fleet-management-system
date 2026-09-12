"""_ensure_document_type() must COMMIT before generate() is called.

The bug, live on Michael's MySQL, 100% reproducible:

    OperationalError (1205, 'Lock wait timeout exceeded')
    INSERT INTO numbering_counters ... scheme_id: 14

A self-deadlock inside a single request, not contention with any other
request:

  1. _ensure_document_type() inserted the VHC NumberingScheme into
     db.session and called flush() -- the row is in the database but
     inside an UNCOMMITTED transaction, holding a lock on it.
  2. generate() then opens a deliberately SEPARATE session. That
     independence is correct and load-bearing: it is what makes an
     issued number survive a caller rollback.
  3. That separate transaction inserts into numbering_counters, whose
     scheme_id foreign key points at the row from step 1. InnoDB must
     take a shared lock on the parent row to validate the FK.
  4. That row is locked by transaction #1, which is itself blocked
     waiting for transaction #2 to return. Neither can proceed.

The v254 retry cannot help: retrying a request that deadlocks against
ITSELF just deadlocks again. Michael's traceback shows the retry ran
(numbering_service.py line 149, `raise last_error`) and still failed,
which is exactly the expected behaviour for a deadlock that is not a
transient race.

Why every other document type was fine: they are all seeded by a CLI
command (app/cli.py), where the commit lands at the end of the whole
seed run, long before any generate() call. This module was the only one
creating its scheme INLINE during a request and immediately numbering
against it.

Why SQLite tests could never catch it: SQLite does not take FK
parent-row locks the way InnoDB does, so the deadlock is not reachable
there at all. These tests therefore assert the INVARIANT that prevents
it -- that nothing is left uncommitted when generate() is reached --
rather than trying to reproduce a lock interaction the test backend
cannot produce.
"""
import pytest

from app.extensions import db
from app.modules.document_config.models import DocumentType, NumberingScheme
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.transactions.vehicle_handover.service import (
    DOCUMENT_TYPE_CODE, VehicleHandoverService)


@pytest.fixture()
def vehicle(db):
    branch = Branch(name="HQ", code="HQ")
    db.session.add(branch)
    db.session.flush()
    vtype = VehicleType(code="CARL", name="Car Light", category="CAR")
    db.session.add(vtype)
    db.session.flush()
    v = Vehicle(plate_number="NAG 1234", brand="Toyota", model="Hilux",
                year=2022, vehicle_type_id=vtype.id, branch_id=branch.id,
                is_active=True, status="ACTIVE")
    db.session.add(v)
    db.session.commit()
    return v


class TestDocumentTypeIsCommittedBeforeNumbering:
    def test_document_type_survives_a_rollback_i_e_was_committed(self, db):
        """The invariant that prevents the deadlock.

        generate() opens an INDEPENDENT session whose INSERT into
        numbering_counters takes an FK lock on the numbering_schemes
        row. If that row is merely flushed, it is locked by the
        caller's still-open transaction, which is itself waiting for
        generate() to return -- the request deadlocks against itself.
        So the row must be genuinely COMMITTED before generate() runs.

        Asserted by rolling back db.session and checking the row is
        still there: committed rows survive, flushed-only rows vanish.
        Backend-independent, and it measures the actual property that
        matters.

        Three weaker checks were tried and REJECTED first, which is
        worth recording so they are not reintroduced:

          * `session.new` / `session.dirty` are EMPTY after flush() --
            flush moves objects to persistent-but-uncommitted, so they
            report clean while the lock is still held. Passes against
            the broken code.
          * Querying from an independent Session also succeeds, because
            the in-memory SQLite test database hands every session the
            same underlying connection.
          * `connection.in_transaction()` is True even BEFORE the call
            and immediately after an explicit commit, because merely
            calling `db.session.connection()` opens a new transaction.
            It reports the check's own side effect, not the bug.
        """
        VehicleHandoverService()._ensure_document_type()
        db.session.rollback()
        dt = DocumentType.query.filter_by(code=DOCUMENT_TYPE_CODE).first()
        assert dt is not None, (
            "the DocumentType did not survive a rollback, so it was only "
            "flushed, not committed; generate()'s independent session "
            "would deadlock on the FK lock this transaction holds")
        assert dt.numbering_scheme is not None, (
            "the NumberingScheme did not survive a rollback -- this is "
            "the exact row generate()'s FK check blocks on")

    def test_calling_it_twice_does_not_create_a_second_scheme(self, db):
        """Idempotence, which the failed attempts on Michael's box cast
        doubt on: his scheme_id went 13 -> 14 across retries. That was
        auto-increment burning ids on rollback rather than genuine
        duplication, but the guarantee is worth pinning down."""
        svc = VehicleHandoverService()
        svc._ensure_document_type()
        svc._ensure_document_type()
        dts = DocumentType.query.filter_by(code=DOCUMENT_TYPE_CODE).all()
        assert len(dts) == 1
        schemes = NumberingScheme.query.filter_by(
            document_type_id=dts[0].id).all()
        assert len(schemes) == 1


class TestCreateStillWorksEndToEnd:
    def test_first_ever_handover_gets_a_number(self, db, vehicle):
        """The user-visible outcome: the very first VHC document of the
        year -- the exact case that 500'd for Michael -- succeeds."""
        doc = VehicleHandoverService().create(vehicle_id=vehicle.id)
        assert doc.document_number.startswith("VHC-")
        assert doc.document_number.endswith("000001")

    def test_second_handover_continues_the_sequence(self, db, vehicle):
        """Committing the doc type early must not disturb the counter."""
        svc = VehicleHandoverService()
        svc.create(vehicle_id=vehicle.id)
        second = svc.create(vehicle_id=vehicle.id)
        assert second.document_number.endswith("000002")
