import pytest

from app.modules.user_management.service import UserService


def test_duplicate_email_is_allowed(db):
    """Regression: previously crashed with an unhandled IntegrityError.
    Login is by username, not email, so duplicate emails are safe."""
    UserService().create_user(username="dupe_user1", email="shared@test.com",
                              password="Pw123456!")
    user2 = UserService().create_user(username="dupe_user2",
                                      email="shared@test.com",
                                      password="Pw123456!")
    assert user2.id is not None
    assert user2.email == "shared@test.com"


def test_duplicate_username_still_rejected(db):
    """Username uniqueness is the real login identifier and must remain
    enforced."""
    from app.modules.user_management.service import DuplicateUsernameError
    UserService().create_user(username="dupe_username_test", email="a@test.com",
                              password="Pw123456!")
    with pytest.raises(DuplicateUsernameError):
        UserService().create_user(username="dupe_username_test",
                                  email="b@test.com", password="Pw123456!")
