"""Fleet Broadcast service.

Generic fleet communication workflow.

Broadcasts are independent of the approval workflow. Publication,
recipient resolution, in-app notification delivery, and acknowledgement
are handled here.
"""

from datetime import datetime, timezone

from app.extensions import db
from app.modules.system_admin.models import (
    FleetBroadcast,
    FleetBroadcastRecipient,
    FleetBroadcastAcknowledgement,
    InAppNotification,
)
from app.modules.user_management.models import User


#: Document Type code the Auto Numbering Engine issues broadcast numbers
#: against. Seeded once via migration (a1f9c3d8e6b2_seed_fb_numbering);
#: prefix, digit count, separator and reset policy stay editable
#: afterwards in System Administration -> Document Type Maintenance.
FLEET_BROADCAST_DOCUMENT_TYPE = "FB"


class FleetBroadcastService:
    TYPES = (
        "ANNOUNCEMENT",
        "ADVISORY",
        "REMINDER",
        "POLICY",
        "NOTICE",
        "SYSTEM",
        "URGENT",
    )

    PRIORITIES = (
        "LOW",
        "NORMAL",
        "HIGH",
        "URGENT",
    )

    RECIPIENT_TYPES = (
        "ALL_USERS",
        "ROLE",
        "SPECIFIC_USER",
        "BRANCH",
        "DEPARTMENT",
    )

    # ------------------------------------------------------------------
    # Basic CRUD
    # ------------------------------------------------------------------

    def list(self, status=None):
        q = FleetBroadcast.query.order_by(FleetBroadcast.id.desc())

        if status:
            q = q.filter_by(status=status)

        return q.all()

    def get(self, broadcast_id):
        return db.session.get(FleetBroadcast, broadcast_id)

    def create(
        self,
        *,
        user,
        broadcast_no,
        broadcast_type,
        category,
        title,
        message,
        priority="NORMAL",
        effective_date=None,
        expiry_date=None,
        recipients=None,
    ):
        broadcast_no = (broadcast_no or "").strip()
        title = (title or "").strip()
        message = message or ""

        if not broadcast_no:
            broadcast_no = self._next_broadcast_no()

        if not title:
            raise ValueError("Title is required.")

        if not message.strip():
            raise ValueError("Message is required.")

        if FleetBroadcast.query.filter_by(
            broadcast_no=broadcast_no
        ).first():
            raise ValueError(
                f"Broadcast number '{broadcast_no}' already exists."
            )

        broadcast_type = (
            broadcast_type or "ANNOUNCEMENT"
        ).upper()

        priority = (
            priority or "NORMAL"
        ).upper()

        category = (
            category or "GENERAL"
        ).strip().upper()

        if broadcast_type not in self.TYPES:
            raise ValueError("Invalid broadcast type.")

        if priority not in self.PRIORITIES:
            raise ValueError("Invalid priority.")

        if (
            effective_date
            and expiry_date
            and expiry_date < effective_date
        ):
            raise ValueError(
                "Expiry date cannot be earlier than effective date."
            )

        row = FleetBroadcast(
            broadcast_no=broadcast_no,
            broadcast_type=broadcast_type,
            category=category,
            title=title,
            message=message,
            priority=priority,
            status="DRAFT",
            effective_date=effective_date,
            expiry_date=expiry_date,
            created_by=user.id,
        )

        db.session.add(row)
        db.session.flush()

        self._replace_recipients(
            row,
            recipients or [],
        )

        db.session.commit()

        return row

    def update(self, broadcast_id, **kwargs):
        row = self.get(broadcast_id)

        if row is None:
            return None

        if row.status != "DRAFT":
            raise ValueError(
                "Only draft broadcasts can be edited."
            )

        for field in (
            "title",
            "message",
            "category",
        ):
            if kwargs.get(field) is not None:
                value = kwargs[field]

                if field != "message":
                    value = value.strip()

                if (
                    field in ("title", "message")
                    and not value.strip()
                ):
                    raise ValueError(
                        f"{field.title()} is required."
                    )

                if field == "category":
                    value = value.upper() or "GENERAL"

                setattr(row, field, value)

        if kwargs.get("broadcast_type") is not None:
            value = kwargs["broadcast_type"].upper()

            if value not in self.TYPES:
                raise ValueError("Invalid broadcast type.")

            row.broadcast_type = value

        if kwargs.get("priority") is not None:
            value = kwargs["priority"].upper()

            if value not in self.PRIORITIES:
                raise ValueError("Invalid priority.")

            row.priority = value

        if kwargs.get("effective_date") is not None:
            row.effective_date = kwargs["effective_date"]

        if kwargs.get("expiry_date") is not None:
            row.expiry_date = kwargs["expiry_date"]

        if (
            row.effective_date
            and row.expiry_date
            and row.expiry_date < row.effective_date
        ):
            raise ValueError(
                "Expiry date cannot be earlier than effective date."
            )

        if kwargs.get("recipients") is not None:
            self._replace_recipients(
                row,
                kwargs["recipients"],
            )

        db.session.commit()

        return row

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(self, broadcast_id, *, user):
        row = self.get(broadcast_id)

        if row is None:
            return None

        if row.status != "DRAFT":
            raise ValueError(
                "Only draft broadcasts can be published."
            )

        if not row.title.strip():
            raise ValueError(
                "Title is required before publishing."
            )

        if not row.message.strip():
            raise ValueError(
                "Message is required before publishing."
            )

        if not row.recipients:
            raise ValueError(
                "At least one broadcast audience is required."
            )

        # Resolve the audience BEFORE changing the status.
        recipients = self._resolve_recipients(row)

        if not recipients:
            raise ValueError(
                "The broadcast audience does not contain any active users."
            )

        # Publish the business record.
        row.status = "PUBLISHED"
        row.published_by = user.id
        row.published_at = datetime.now(timezone.utc)

        # Create the notification records.
        self._create_notifications(
            row,
            recipients,
        )

        db.session.commit()

        return row

    # ------------------------------------------------------------------
    # Acknowledgement
    # ------------------------------------------------------------------

    def acknowledge(self, broadcast_id, *, user):
        row = self.get(broadcast_id)

        if row is None:
            return None

        if row.status != "PUBLISHED":
            raise ValueError(
                "Only published broadcasts can be acknowledged."
            )

        # A user should only acknowledge a broadcast intended for them.
        eligible_users = self._resolve_recipients(row)

        if user.id not in eligible_users:
            raise ValueError(
                "You are not an intended recipient of this broadcast."
            )

        ack = FleetBroadcastAcknowledgement.query.filter_by(
            broadcast_id=row.id,
            user_id=user.id,
        ).first()

        if ack is None:
            ack = FleetBroadcastAcknowledgement(
                broadcast_id=row.id,
                user_id=user.id,
                acknowledged_at=datetime.now(timezone.utc),
            )

            db.session.add(ack)
            db.session.commit()

        return ack

    def acknowledgements(self, broadcast_id):
        return (
            FleetBroadcastAcknowledgement.query
            .filter_by(broadcast_id=broadcast_id)
            .order_by(
                FleetBroadcastAcknowledgement.id.desc()
            )
            .all()
        )

    # ------------------------------------------------------------------
    # Recipient maintenance
    # ------------------------------------------------------------------

    def _replace_recipients(self, broadcast, recipients):
        broadcast.recipients.clear()

        for item in recipients:
            if not isinstance(item, dict):
                raise ValueError(
                    "Each recipient must be an object."
                )

            recipient_type = (
                item.get("recipient_type")
                or item.get("type")
                or ""
            ).upper()

            if recipient_type not in self.RECIPIENT_TYPES:
                raise ValueError(
                    f"Invalid recipient type '{recipient_type}'."
                )

            row = FleetBroadcastRecipient(
                recipient_type=recipient_type,
                role_id=self._int_or_none(
                    item.get("role_id")
                ),
                user_id=self._int_or_none(
                    item.get("user_id")
                ),
                branch_id=self._int_or_none(
                    item.get("branch_id")
                ),
                department_id=self._int_or_none(
                    item.get("department_id")
                ),
            )
            broadcast.recipients.append(row)

            required = {
                "ROLE": ("role_id", row.role_id),
                "SPECIFIC_USER": ("user_id", row.user_id),
                "BRANCH": ("branch_id", row.branch_id),
                "DEPARTMENT": (
                    "department_id",
                    row.department_id,
                ),
            }.get(recipient_type)

            if required and not required[1]:
                raise ValueError(
                    f"{recipient_type} recipient requires "
                    f"{required[0]}."
                )

            db.session.add(row)

    # ------------------------------------------------------------------
    # Recipient resolution
    # ------------------------------------------------------------------
    def user_matches_broadcast_audience(self, broadcast, user):
        """Return True when `user` belongs to `broadcast`'s audience.

        The single source of truth for "who receives this broadcast."
        `_resolve_recipients` below answers the same question in bulk, for
        the DB-backed fan-out on publish; this answers it for one already-
        loaded user, for `/my/fleet-broadcasts` and the acknowledge/ack-status
        endpoints. Two independently maintained copies of this rule is how
        they drift -- this one checks `is_active` on the user and on the role
        for exactly the reason `_resolve_recipients` does: a deactivated
        account or role should stop receiving broadcasts, not just stop
        being notified of new ones.
        """
        if not user.is_active:
            return False

        for recipient in broadcast.recipients:
            recipient_type = recipient.recipient_type

            if recipient_type == "ALL_USERS":
                return True

            elif recipient_type == "SPECIFIC_USER":
                if recipient.user_id == user.id:
                    return True

            elif recipient_type == "ROLE":
                if recipient.role_id and any(
                    getattr(role, "id", None) == recipient.role_id
                    and getattr(role, "is_active", True)
                    for role in (user.roles or [])
                ):
                    return True

            elif recipient_type == "BRANCH":
                if (
                    recipient.branch_id is not None
                    and recipient.branch_id == getattr(user, "branch_id", None)
                ):
                    return True

            elif recipient_type == "DEPARTMENT":
                if (
                    recipient.department_id is not None
                    and recipient.department_id
                    == getattr(user, "department_id", None)
                ):
                    return True

        return False

    def _resolve_recipients(self, broadcast):
        """Return unique active User IDs matching the broadcast audience.

        A user can match multiple recipient rows. The returned set of
        IDs therefore removes duplicates before notifications are created.
        """

        user_ids = set()

        for recipient in broadcast.recipients:

            recipient_type = recipient.recipient_type

            if recipient_type == "ALL_USERS":
                matches = (
                    User.query
                    .filter_by(is_active=True)
                    .with_entities(User.id)
                    .all()
                )

                user_ids.update(
                    user_id
                    for (user_id,) in matches
                )

            elif recipient_type == "ROLE":
                matches = (
                    User.query
                    .join(User.roles)
                    .filter(
                        User.is_active.is_(True),
                        User.roles.any(
                            id=recipient.role_id,
                            is_active=True,
                        ),
                    )
                    .with_entities(User.id)
                    .all()
                )

                user_ids.update(
                    user_id
                    for (user_id,) in matches
                )

            elif recipient_type == "SPECIFIC_USER":
                user = (
                    User.query
                    .filter(
                        User.id == recipient.user_id,
                        User.is_active.is_(True),
                    )
                    .first()
                )

                if user:
                    user_ids.add(user.id)

            elif recipient_type == "BRANCH":
                matches = (
                    User.query
                    .filter(
                        User.is_active.is_(True),
                        User.branch_id == recipient.branch_id,
                    )
                    .with_entities(User.id)
                    .all()
                )

                user_ids.update(
                    user_id
                    for (user_id,) in matches
                )

            elif recipient_type == "DEPARTMENT":
                matches = (
                    User.query
                    .filter(
                        User.is_active.is_(True),
                        User.department_id
                        == recipient.department_id,
                    )
                    .with_entities(User.id)
                    .all()
                )

                user_ids.update(
                    user_id
                    for (user_id,) in matches
                )

        return user_ids

    # ------------------------------------------------------------------
    # Notification delivery
    # ------------------------------------------------------------------

    def _create_notifications(self, broadcast, user_ids):
        """Create one in-app notification per unique recipient."""

        if not user_ids:
            return 0

        # Protect against duplicate notification creation if this method
        # is ever called with repeated IDs.
        user_ids = set(user_ids)

        existing = (
            InAppNotification.query
            .filter(
                InAppNotification.event_code
                == "fleet_broadcast",
                InAppNotification.reference_table
                == "fleet_broadcasts",
                InAppNotification.reference_id
                == broadcast.id,
                InAppNotification.user_id.in_(user_ids),
            )
            .with_entities(InAppNotification.user_id)
            .all()
        )

        existing_user_ids = {
            user_id
            for (user_id,) in existing
        }

        notifications = []

        for user_id in sorted(
            user_ids - existing_user_ids
        ):
            notifications.append(
                InAppNotification(
                    user_id=user_id,
                    title=broadcast.title,
                    message=broadcast.message,
                    event_code="fleet_broadcast",
                    reference_table="fleet_broadcasts",
                    reference_id=broadcast.id,
                    is_read=False,
                )
            )

        if notifications:
            db.session.add_all(notifications)

        return len(notifications)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _next_broadcast_no():
        """Issue the next number from the generic Auto Numbering Engine.

        Deliberately the same engine every other document uses rather than
        a local counter, so a broadcast series is configured in System
        Administration -> Document Type Maintenance like TT/MO/PR/ATD and
        inherits the engine's independent-transaction guarantee: a number,
        once issued, is never reissued even if this create() rolls back.

        NoSchemeError is translated because it means the FB document type
        has not been configured yet -- an admin-fixable setup gap, which
        the API should report as a 400 the admin can act on rather than a
        500 that reads as a broken system.
        """
        from app.core.numbering.numbering_service import (
            AutoNumberingService, NoSchemeError)

        try:
            return AutoNumberingService().generate(
                FLEET_BROADCAST_DOCUMENT_TYPE)
        except NoSchemeError:
            raise ValueError(
                f"No active numbering scheme for document type "
                f"'{FLEET_BROADCAST_DOCUMENT_TYPE}'. Configure it under "
                f"System Administration -> Document Type Maintenance, or "
                f"supply a broadcast number explicitly."
            )

    @staticmethod
    def _int_or_none(value):
        if value in (None, ""):
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(
                "Recipient IDs must be numeric."
            )