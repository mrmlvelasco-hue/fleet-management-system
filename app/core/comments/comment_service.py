"""Generic comment/discussion-thread service — usable by any module via
reference_table + reference_id, same pattern as AttachmentService."""
from app.extensions import db
from app.core.comments.models import DocumentComment


class EmptyCommentError(Exception):
    pass


class CommentService:
    def create(self, *, reference_table, reference_id, author, body,
               recipient=None):
        text = (body or "").strip()
        if not text:
            raise EmptyCommentError("Comment cannot be empty.")
        comment = DocumentComment(
            reference_table=reference_table, reference_id=reference_id,
            author_id=author.id, recipient_id=recipient.id if recipient else None,
            body=text)
        db.session.add(comment)
        db.session.commit()
        return comment

    def list_for(self, reference_table, reference_id) -> list:
        return (DocumentComment.query
               .filter_by(reference_table=reference_table,
                         reference_id=reference_id)
               .order_by(DocumentComment.id.asc())
               .all())
