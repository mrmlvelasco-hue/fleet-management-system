from app.core.security.password import hash_password
from app.modules.user_management.models import User
from app.core.comments.comment_service import CommentService


def _make_user(db, username):
    u = User(username=username, email=f"{username}@x.com",
             password_hash=hash_password("pw123456"))
    db.session.add(u)
    db.session.commit()
    return u


def test_post_comment_on_a_document(db):
    author = _make_user(db, "comment_author1")
    comment = CommentService().create(
        reference_table="maintenance_orders", reference_id=1,
        author=author, body="Please expedite this order.")
    assert comment.body == "Please expedite this order."
    assert comment.author_id == author.id
    assert comment.reference_table == "maintenance_orders"
    assert comment.reference_id == 1


def test_post_comment_with_optional_recipient(db):
    author = _make_user(db, "comment_author2")
    recipient = _make_user(db, "comment_recipient2")
    comment = CommentService().create(
        reference_table="maintenance_orders", reference_id=2,
        author=author, body="Can you review this?", recipient=recipient)
    assert comment.recipient_id == recipient.id


def test_comment_recipient_is_optional(db):
    author = _make_user(db, "comment_author3")
    comment = CommentService().create(
        reference_table="maintenance_orders", reference_id=3,
        author=author, body="General note.")
    assert comment.recipient_id is None


def test_list_comments_for_a_document_ordered_oldest_first(db):
    author = _make_user(db, "comment_author4")
    CommentService().create(reference_table="trip_tickets", reference_id=4,
                            author=author, body="First comment")
    CommentService().create(reference_table="trip_tickets", reference_id=4,
                            author=author, body="Second comment")
    # A comment on a DIFFERENT document should not show up
    CommentService().create(reference_table="trip_tickets", reference_id=99,
                            author=author, body="Unrelated")

    comments = CommentService().list_for("trip_tickets", 4)
    assert len(comments) == 2
    assert comments[0].body == "First comment"
    assert comments[1].body == "Second comment"


def test_empty_body_is_rejected(db):
    from app.core.comments.comment_service import EmptyCommentError
    author = _make_user(db, "comment_author5")
    import pytest
    with pytest.raises(EmptyCommentError):
        CommentService().create(reference_table="trip_tickets", reference_id=5,
                                author=author, body="   ")
