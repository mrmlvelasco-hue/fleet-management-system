"""Regression test for the second half of the reported production bug.

A database-related exception (the numbering collision fixed alongside
this) leaves the SQLAlchemy session in a "pending rollback" state --
proven directly below: the very next query against that session fails
with PendingRollbackError, not just the one that originally failed.

Our own branded 500 page (errors/500.html, via base.html) needs
current_user.is_authenticated, which is a database lookup. Without an
explicit rollback first, rendering THAT page hits the same
PendingRollbackError a second time -- which is exactly why the reported
screenshot showed Flask's raw unstyled fallback page instead of our own
page with its reference code: our page never got the chance to render
at all.
"""
import pytest
from sqlalchemy.exc import IntegrityError, PendingRollbackError


def test_a_failed_commit_poisons_the_session_for_the_next_query(db):
    """Establishes the mechanism this fix addresses: SQLAlchemy's
    documented behaviour is that a session stays broken after a failed
    flush/commit until rollback() is called -- proven directly rather
    than assumed, since this is the exact condition the 500 handler
    must clear before it can render anything."""
    from app.modules.user_management.models import User

    db.session.add(User(username="dupe500a", email="a@a.com",
                        password_hash="x"))
    db.session.add(User(username="dupe500a", email="b@b.com",
                        password_hash="x"))
    with pytest.raises(IntegrityError):
        db.session.commit()

    with pytest.raises(PendingRollbackError):
        User.query.count()

    db.session.rollback()
    assert User.query.count() == 0


def test_500_handler_renders_successfully_on_a_broken_session(app, db):
    """The real behaviour that matters, tested directly rather than by
    inspecting source text: call the ACTUAL registered handler function
    with the session already broken (reproducing the exact reported
    state), inside a real request context, and confirm it produces a
    500 response instead of raising PendingRollbackError itself while
    trying to render its own error page."""
    from app.modules.user_management.models import User
    from werkzeug.exceptions import InternalServerError

    handler = app.error_handler_spec[None][500][InternalServerError]

    with app.test_request_context("/__wherever"):
        from flask_login import login_user
        admin = User.query.first()
        if admin is None:
            admin = User(username="handlertest", email="h@h.com",
                        password_hash="x")
            db.session.add(admin)
            db.session.commit()
        login_user(admin)

        # Break the session exactly as the reported bug does.
        db.session.add(User(username="dupe500b", email="c@c.com",
                            password_hash="x"))
        db.session.add(User(username="dupe500b", email="d@d.com",
                            password_hash="x"))
        with pytest.raises(IntegrityError):
            db.session.commit()

        # This is the actual behaviour under test: calling the real
        # handler with a broken session must not itself raise
        # PendingRollbackError -- it must roll back and render.
        response = handler(InternalServerError())
        body, status = response if isinstance(response, tuple) else (response, 200)
        assert status == 500
        text = body.get_data(as_text=True) if hasattr(body, "get_data") else body
        assert "reference" in text.lower()
