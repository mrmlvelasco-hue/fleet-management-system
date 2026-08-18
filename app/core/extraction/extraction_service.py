"""Applying extracted fields to a vehicle -- with a gate.

The rule, set by the client: a field the system is not confident about
must be CHECKED by a person before it can be saved. Not blocked outright
-- they may well have the paper in front of them and know the reading is
right -- but never written silently.

That distinction matters because of what OCR gets wrong here. A chassis
number two characters out looks entirely plausible on screen; nobody
reviewing a form would catch it. Requiring an explicit confirmation puts
a human eye on exactly the values that need one, and leaves the rest of
the form as fast as it was.

Extraction proposes; the person disposes. Nothing is written that was
not explicitly selected, and the vehicle stays editable afterwards like
any other record.
"""
from app.extensions import db


class UnconfirmedFieldsError(Exception):
    """Raised when a field needing review was selected but not
    confirmed. Carries the field names so the UI can point at them."""

    def __init__(self, fields):
        self.fields = list(fields)
        super().__init__(
            "These readings need checking against the document before "
            "they can be saved: " + ", ".join(self.fields))


class ExtractionService:

    def apply(self, vehicle, fields, selected, confirmed=None,
              user=None):
        """Write the selected fields onto the vehicle.

        `fields`    -- {column: ExtractedField} from a parser
        `selected`  -- columns the person ticked
        `confirmed` -- columns they explicitly confirmed having checked

        Returns {column: value} of what was actually written.
        """
        selected = set(selected or [])
        confirmed = set(confirmed or [])

        # Check BEFORE writing anything: a partial apply that stopped
        # halfway would leave the record in a state nobody chose.
        unconfirmed = [
            name for name in selected
            if name in fields
            and fields[name].needs_review
            and name not in confirmed
        ]
        if unconfirmed:
            raise UnconfirmedFieldsError(unconfirmed)

        applied = {}
        for name in selected:
            item = fields.get(name)
            if item is None or not item.value:
                continue
            if not hasattr(vehicle, name):
                # A parser field with no matching column would otherwise
                # fail silently; skipping is right, but it is a bug in
                # the parser rather than user error.
                continue
            value = item.value
            # `year` is an integer column; everything else the CR gives
            # us is text.
            if name == "year":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
            setattr(vehicle, name, value)
            applied[name] = value

        if applied:
            db.session.commit()
        return applied

    def propose(self, fields):
        """What to offer the person, and what merely to report.

        Extraction NEVER becomes a precondition for entering a vehicle.
        If a scan yields nothing, the form behaves exactly as it always
        has -- this feature can only ever save typing, never gate it.
        """
        from app.core.extraction.cr_parser import high_confidence_only
        trusted, unverified = high_confidence_only(fields)
        return {
            "trusted": trusted,
            "unverified": unverified,
            "message": self._message(trusted, unverified),
        }

    @staticmethod
    def _message(trusted, unverified):
        if not trusted and not unverified:
            return ("Nothing could be read from this scan. Please enter "
                    "the details manually — scanning in black and white "
                    "at 300 DPI usually reads much better.")
        if not trusted:
            return (f"{len(unverified)} value(s) were read but none could be "
                    f"verified, so nothing has been filled in. Check them "
                    f"against the document and type what you need.")
        note = (f"{len(trusted)} field(s) verified and ready to apply.")
        if unverified:
            note += (f" {len(unverified)} more were read but could not be "
                    f"verified — shown for reference only, please type "
                    f"those yourself.")
        return note

    def summarise(self, fields):
        """Counts for the review panel header."""
        total = len(fields)
        needs = sum(1 for f in fields.values() if f.needs_review)
        return {"total": total, "needs_review": needs,
                "ready": total - needs}
