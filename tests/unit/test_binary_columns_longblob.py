"""Every binary column must be LONGBLOB on MySQL.

A standing guard for a failure the test suite cannot otherwise see.

SQLAlchemy's LargeBinary maps to MySQL BLOB -- 65,535 bytes. SQLite has
no such limit, so a column declared as plain LargeBinary passes every
test and then fails on the client's server the first time somebody
uploads anything real:

    (1406, "Data too long for column 'file_data' at row 1")

That is exactly what happened to mobile_app_release_files.file_data with
a 13.7 MB APK. attachments.file_data already used the variant; the new
table did not follow the pattern, and nothing noticed.

This test reads the METADATA rather than the database, so it works on
SQLite and catches the next one at the moment the column is written.
"""
import pytest
from sqlalchemy import LargeBinary
from sqlalchemy.dialects import mysql

from app.extensions import db


def _binary_columns(app):
    """Every binary column across all registered models."""
    found = []
    for table in db.metadata.sorted_tables:
        for column in table.columns:
            if isinstance(column.type, (LargeBinary, mysql.LONGBLOB,
                                        mysql.MEDIUMBLOB, mysql.BLOB)):
                found.append((table.name, column.name, column.type))
    return found


def test_there_are_binary_columns_to_check(app):
    """Guards the guard. If the discovery above silently found nothing,
    every assertion below would vacuously pass."""
    assert _binary_columns(app), "no binary columns discovered"


@pytest.mark.parametrize("expected", ["attachments", "mobile_app_release_files"])
def test_the_known_binary_tables_are_still_discovered(app, expected):
    assert expected in {t for t, _c, _ty in _binary_columns(app)}


def test_every_binary_column_is_longblob_on_mysql(app):
    """The rule.

    A LargeBinary without a MySQL variant is a 64 KB ceiling on a column
    meant to hold a file -- and it will only ever be discovered in
    production, by a user, with a stack trace.
    """
    offenders = []
    for table, column, type_ in _binary_columns(app):
        mysql_type = type_.dialect_impl(mysql.dialect())
        if not isinstance(mysql_type, (mysql.LONGBLOB, mysql.MEDIUMBLOB)):
            offenders.append(f"{table}.{column} -> "
                             f"{mysql_type.__class__.__name__}")
    assert not offenders, (
        "These columns map to MySQL BLOB (65,535 bytes) and will fail on "
        "any real file. Use "
        "db.LargeBinary().with_variant(LONGBLOB, 'mysql'): "
        + ", ".join(offenders))
