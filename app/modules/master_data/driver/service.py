"""Driver/Assignee master service."""
from datetime import date, timedelta

from app.extensions import db
from app.modules.master_data.driver.models import Driver, EmergencyContact


class DuplicateDriverError(Exception):
    pass


class InvalidAssigneeError(Exception):
    pass


class DuplicateAssigneeLinkError(Exception):
    """One system account is already linked to a different assignee.

    Distinct from DuplicateDriverError, which is about duplicate master
    data (a licence or employee number entered twice). This is about the
    login-to-person join, and the caller handles it differently: a
    duplicate employee number is a data-entry mistake, while this is
    usually an administrator picking the wrong name from a list and
    needs to name the assignee already holding the account.
    """
    pass


class EmergencyContactService:
    def create(self, *, person_record_id, contact_name, relationship_type=None,
              contact_number=None):
        contact = EmergencyContact(
            person_record_id=person_record_id, contact_name=contact_name,
            relationship_type=relationship_type, contact_number=contact_number)
        db.session.add(contact)
        db.session.commit()
        return contact

    def list_for_person(self, person_record_id) -> list:
        return (EmergencyContact.query
               .filter_by(person_record_id=person_record_id, is_active=True)
               .order_by(EmergencyContact.id).all())

    def delete(self, contact_id):
        contact = db.session.get(EmergencyContact, contact_id)
        if contact:
            contact.is_active = False
            db.session.commit()


class DriverService:
    def create(self, employee_number, first_name, last_name,
               branch_id, assignee_type="DRIVER", license_number=None,
               license_expiry=None, license_type=None, photo_file=None,
               user=None, **kwargs):
        # Both creation constraints below are CONFIGURABLE, because both
        # block the client's migration from manual records: create()
        # raises, so a legacy assignee whose paper file lacks a licence
        # or a photograph cannot be entered at all. The record would
        # have to be falsified or left out, and a driver missing from
        # the roster is worse than one with an incomplete record.
        #
        # Read PER CALL, never cached at import: toggling a parameter in
        # System Administration has to take effect immediately, and a
        # rule that needed a restart is one nobody would connect to the
        # switch they just flipped.
        #
        # Defaults are STRICT. An install predating these parameters
        # behaves exactly as it did before; the permissive setting is an
        # explicit choice recorded in the database, not something a
        # missing row can cause by accident.
        from app.modules.system_admin.services.system_parameter_service import (
            SystemParameterService)
        params = SystemParameterService()

        # DRIVER-type assignees keep the original requirement — license
        # details are mandatory. Every other assignee type (Employee,
        # Consultant, Third Party Delivery) can be assigned a vehicle
        # without personally holding a license on file, which is
        # unchanged and independent of the parameter.
        if params.get("REQUIRE_DRIVER_LICENSE", default=True):
            if assignee_type == "DRIVER" and not (
                    license_number and license_expiry and license_type):
                raise InvalidAssigneeError(
                    "License Number, Expiry, and Type are required for a "
                    "Driver-type assignee.")

        # A photo is required for EVERY new assignee, regardless of
        # type -- it prints on the Vehicle Assignment Memo and the
        # Vehicle Issuance / Receiving Checklist for any assignee, not
        # just literal drivers. Checked here, before anything is
        # written, so a missing photo never leaves a half-created
        # record behind.
        #
        # Independent of the licence parameter. They exist for the same
        # migration but describe different facts, and collapsing them
        # would let one client decision silently make another.
        if params.get("REQUIRE_ASSIGNEE_PHOTO", default=True):
            if photo_file is None or not getattr(photo_file, "filename", ""):
                raise InvalidAssigneeError(
                    "A photo is required when creating a new driver or "
                    "assignee record.")
        if license_number and Driver.query.filter_by(
                license_number=license_number).first():
            raise DuplicateDriverError(
                f"License number '{license_number}' already exists.")
        if Driver.query.filter_by(
                employee_number=employee_number).first():
            raise DuplicateDriverError(
                f"Employee number '{employee_number}' already exists.")

        person_id = self._generate_person_id()
        obj = Driver(
            person_id=person_id, employee_number=employee_number,
            first_name=first_name, last_name=last_name,
            assignee_type=assignee_type, license_number=license_number,
            license_expiry=license_expiry, license_type=license_type,
            branch_id=branch_id, **kwargs)
        db.session.add(obj)
        db.session.commit()

        # The attachment references this driver's own id, so the upload
        # can only happen once the row (and its id) exist.
        #
        # Guarded now rather than assumed: with REQUIRE_ASSIGNEE_PHOTO
        # off, photo_file may legitimately be absent. Waiving the
        # REQUIREMENT must not disable the feature, so a photo supplied
        # during migration is still stored.
        if photo_file is not None and getattr(photo_file, "filename", ""):
            from app.core.attachments.attachment_service import (
                AttachmentService)
            attachment = AttachmentService().upload(
                photo_file, reference_table="drivers", reference_id=obj.id,
                user=user)
            obj.photo_attachment_id = attachment.id
            db.session.commit()
        return obj

    def _generate_person_id(self) -> str:
        """Simple sequential Person ID — PID-<year>-<zero-padded count>.
        Not a full document-numbering-engine document (this is master
        data, not a transaction), but still unique and traceable."""
        year = date.today().year
        count = Driver.query.filter(
            Driver.person_id.like(f"PID-{year}-%")).count()
        return f"PID-{year}-{count + 1:06d}"

    def update(self, record_id, photo_file=None, user=None, **kwargs):
        obj = db.session.get(Driver, record_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
            # Optional: replacing an existing photo, or adding one to a
            # record that predates this field. Never required here --
            # only at creation -- so editing an unrelated field on an
            # existing driver can't be blocked by a missing photo.
            if photo_file is not None and getattr(photo_file, "filename", ""):
                from app.core.attachments.attachment_service import (
                    AttachmentService)
                attachment = AttachmentService().upload(
                    photo_file, reference_table="drivers",
                    reference_id=obj.id, user=user)
                obj.photo_attachment_id = attachment.id
            db.session.commit()
        return obj

    def link_user(self, record_id, user_id):
        """Point this assignee at a system account, or clear the link.

        Kept out of update() and off the generic **kwargs path on
        purpose. update() sets whatever it is handed; this has rules,
        and a rule that can be bypassed by passing the same field
        through a different door is not a rule. Everything that writes
        drivers.user_id goes through here.

        Passing None clears the link. Clearing never touches the account
        itself -- the person keeps signing in to the web app exactly as
        before, because being an assignee and having a login are
        separate facts.
        """
        from app.modules.user_management.models import User

        obj = db.session.get(Driver, record_id)
        if obj is None:
            return None

        if user_id in (None, "", 0):
            obj.user_id = None
            db.session.commit()
            return obj

        user_id = int(user_id)
        # Re-linking to the account already held is a no-op, not a
        # duplicate. Without this, saving the assignee form a second
        # time without touching the picker would be rejected -- so an
        # ordinary edit to an unrelated field on a linked assignee
        # would fail.
        if obj.user_id == user_id:
            return obj

        user = db.session.get(User, user_id)
        if user is None or not user.is_active:
            raise InvalidAssigneeError(
                "That system account does not exist or is inactive.")

        # Checked in the service as well as by the UNIQUE constraint.
        # The constraint is the backstop that keeps the data correct
        # under any writer; this is what turns a violation into a
        # sentence an administrator can act on instead of a 500 from an
        # IntegrityError raised at commit time, three layers away from
        # the form they submitted.
        clash = Driver.query.filter(Driver.user_id == user_id,
                                    Driver.id != obj.id).first()
        if clash is not None:
            raise DuplicateAssigneeLinkError(
                f"System account '{user.username}' is already linked to "
                f"assignee {clash.full_name} ({clash.employee_number}).")

        # Deliberately does NOT set user.mobile_access. Linking records
        # WHO someone is; the flag records WHETHER their phone may hold
        # a credential. Granting one from the other would mean every
        # assignee linked for reporting silently gained the field app.
        obj.user_id = user_id
        db.session.commit()
        return obj

    def get(self, record_id, include_inactive=True):
        return db.session.get(Driver, record_id)

    def get_visible(self, record_id, user):
        """Like get(), but returns None if `user` doesn't have visibility
        into this driver per organizational scope."""
        obj = db.session.get(Driver, record_id)
        if obj is None:
            return None
        if user is None:
            return obj
        if obj.created_by == getattr(user, "id", None):
            return obj
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        if UserOrgScopeService().covers(user.id, branch_id=obj.branch_id):
            return obj
        return None

    def list(self, include_inactive=False, branch_id=None, user=None):
        q = Driver.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if branch_id:
            q = q.filter_by(branch_id=branch_id)
        records = q.order_by(Driver.last_name, Driver.first_name).all()
        if user is None:
            return records
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return [d for d in records
               if d.created_by == getattr(user, "id", None)
               or scope_svc.covers(user.id, branch_id=d.branch_id)]

    def get_expiring_licenses(self, days=30):
        """Drivers whose license expires within `days` days."""
        threshold = date.today() + timedelta(days=days)
        return (Driver.query
                .filter(Driver.is_active.is_(True),
                        Driver.license_expiry <= threshold)
                .order_by(Driver.license_expiry)
                .all())

    def deactivate(self, record_id):
        obj = db.session.get(Driver, record_id)
        if obj:
            obj.is_active = False
            obj.status = "INACTIVE"
            db.session.commit()

    def reactivate(self, record_id):
        obj = db.session.get(Driver, record_id)
        if obj:
            obj.is_active = True
            obj.status = "ACTIVE"
            db.session.commit()
