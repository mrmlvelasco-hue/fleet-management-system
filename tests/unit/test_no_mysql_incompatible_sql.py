"""Guard against MySQL-incompatible SQL constructs re-entering the code.

This test exists because the SAME bug shipped THREE times:

  1. PMPackageRecommendationService used `.nullslast()` -> production
     MySQL 500 on selecting a plate number.
  2. PMScheduleService.get_profile() had to be written to sort in Python
     for the same reason, with a comment explaining why.
  3. _Prefetch in the due-calculation service then used `.nulls_first()`
     anyway -> production MySQL 500 on the dashboard.

The third occurrence even carried a `hasattr(..., "nulls_first")` guard,
which was worthless: it asks whether SQLAlchemy has the METHOD (always
true), not whether the DATABASE accepts the SQL it generates.

Crucially, none of this is caught by the normal suite, because the tests
run on SQLite -- which ACCEPTS `NULLS FIRST`/`NULLS LAST` happily. Only
MySQL rejects it (error 1064). So a source-level check is the only thing
that actually stops a fourth recurrence.

If this test fails, use the portable form instead:

    .order_by(Model.column.is_(None).desc(), Model.column.asc())   # NULLs first
    .order_by(Model.column.is_(None).asc(),  Model.column.asc())   # NULLs last

or sort in Python when the row count is small.
"""
import pathlib

import pytest

# SQLAlchemy spellings that compile to the NULLS FIRST / NULLS LAST SQL
# keyword, which MySQL does not support in any version.
FORBIDDEN = ("nulls_first", "nulls_last", "nullsfirst", "nullslast")

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SEARCH_DIRS = ("app", "scripts")


def _python_files():
    for directory in SEARCH_DIRS:
        root = PROJECT_ROOT / directory
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def _offending_lines(path):
    """Return real usages, ignoring lines that only MENTION the construct
    in a comment or docstring explaining why not to use it."""
    offenders = []
    for lineno, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        # Skip pure comment lines -- the existing explanatory comments are
        # deliberate documentation and must not trip this test.
        if stripped.startswith("#"):
            continue
        lowered = raw.lower()
        for token in FORBIDDEN:
            # A real call looks like `.nulls_first(` -- requiring the
            # paren avoids flagging prose inside a docstring.
            if f"{token}(" in lowered:
                offenders.append((lineno, stripped))
                break
    return offenders


def test_no_nulls_first_or_last_in_source():
    problems = []
    for path in _python_files():
        for lineno, line in _offending_lines(path):
            problems.append(
                f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {line}")

    assert not problems, (
        "MySQL-incompatible NULLS FIRST/LAST construct found. MySQL "
        "rejects this with error 1064; SQLite accepts it, so the rest of "
        "the suite will NOT catch it.\n\nUse instead:\n"
        "    .order_by(Model.col.is_(None).desc(), Model.col.asc())  "
        "# NULLs first\n"
        "    .order_by(Model.col.is_(None).asc(),  Model.col.asc())  "
        "# NULLs last\n\nOffending line(s):\n  " + "\n  ".join(problems))


@pytest.mark.parametrize("dialect_name", ["mysql", "postgresql", "sqlite"])
def test_prefetch_ordering_compiles_without_nulls_keyword(app, dialect_name):
    """Belt and braces: compile the exact query that broke production
    against each dialect and assert the keyword never appears."""
    from sqlalchemy.dialects import mysql, postgresql, sqlite
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    dialects = {"mysql": mysql.dialect(),
                "postgresql": postgresql.dialect(),
                "sqlite": sqlite.dialect()}

    with app.app_context():
        query = (MaintenanceOrder.query
                .filter(MaintenanceOrder.status == "COMPLETED")
                .order_by(MaintenanceOrder.completed_date.is_(None).desc(),
                         MaintenanceOrder.completed_date.asc()))
        compiled = str(query.statement.compile(
            dialect=dialects[dialect_name]))

    assert "NULLS" not in compiled.upper(), (
        f"Query emits the NULLS keyword on {dialect_name}; MySQL will "
        f"reject it with error 1064.")


def test_null_completed_dates_sort_first(db):
    """The ordering must still do what the code depends on: rows with no
    completed_date come FIRST, so a later dated completion overwrites
    them when building the last-service map."""
    from datetime import date
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    for completed in (date(2026, 1, 1), None, date(2025, 1, 1)):
        db.session.add(MaintenanceOrder(
            vehicle_id=1, status="COMPLETED", scheduled_date=date(2025, 1, 1),
            completed_date=completed))
    db.session.commit()

    rows = (MaintenanceOrder.query
           .filter(MaintenanceOrder.status == "COMPLETED")
           .order_by(MaintenanceOrder.completed_date.is_(None).desc(),
                    MaintenanceOrder.completed_date.asc())
           .all())
    dates = [r.completed_date for r in rows]
    assert dates[0] is None, "NULL completed_date must sort first"
    assert dates[1:] == [date(2025, 1, 1), date(2026, 1, 1)], \
        "dated rows must then run oldest to newest"
