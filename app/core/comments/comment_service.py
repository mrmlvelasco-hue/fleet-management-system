"""Generic comment/discussion-thread service — usable by any module via
reference_table + reference_id, same pattern as the AttachmentService.
"""
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

        # The "notify" picker in the UI implies the selected person will
        # actually be told about the comment -- previously recipient_id was
        # only stored, never acted on. Fire both channels the same way
        # every other event in the app does (InAppNotification row +
        # queued email via the DOCUMENT_COMMENT template), so a mention
        # here behaves identically to an approval notification.
        if recipient is not None and recipient.id != author.id:
            self._notify_recipient(comment, recipient, author)

        return comment

    def _notify_recipient(self, comment, recipient, author) -> None:
        from app.modules.system_admin.models import InAppNotification
        preview = comment.body[:120] + ("…" if len(comment.body) > 120 else "")
        db.session.add(InAppNotification(
            user_id=recipient.id,
            title=f"{author.full_name} mentioned you in a comment",
            message=preview,
            event_code="DOCUMENT_COMMENT",
            reference_table=comment.reference_table,
            reference_id=comment.reference_id))
        db.session.commit()

        try:
            from app.modules.system_admin.tasks import dispatch_notification_email
            dispatch_notification_email(
                user_id=recipient.id, event_code="DOCUMENT_COMMENT",
                reference_table=comment.reference_table,
                reference_id=comment.reference_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Comment notification email failed entirely (both queued "
                "and synchronous attempts). In-app notification was still "
                "delivered.")

    def list_for(self, reference_table, reference_id) -> list:
        return (DocumentComment.query
               .filter_by(reference_table=reference_table,
                         reference_id=reference_id)
               .order_by(DocumentComment.id.asc())
               .all())
