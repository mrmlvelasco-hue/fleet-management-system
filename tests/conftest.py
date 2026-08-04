import sys
from pathlib import Path

import pytest

# A handful of test modules (test_import_vems_makemodel.py,
# test_import_vems_pms.py, test_import_pm_task_list.py,
# test_vems_import_bugfixes.py) import one-off VEMS migration helpers
# that live in scripts/, not inside the app package. Without this,
# `pytest tests/unit` fails at COLLECTION -- before a single test runs --
# on any machine that hasn't manually exported PYTHONPATH to include
# scripts/, which in practice was every fresh clone including the one
# that surfaced this.
#
# Inserted here, in the top-level tests/conftest.py, because pytest
# loads this before collecting any test module in the tree, so it's the
# one place a path fix reaches every affected file regardless of which
# subdirectory they're in or the order pytest walks the tree.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if _SCRIPTS_DIR.is_dir():
    sys.path.insert(0, str(_SCRIPTS_DIR))

from app import create_app
from app.extensions import db as _db


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()
    # Clear approval-engine event subscribers registered during the test.
    from app.core.approval import engine as _engine
    _engine._subscribers.clear()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    return _db
