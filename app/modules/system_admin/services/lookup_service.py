"""Lookup Maintenance service — generic code-table for dropdowns."""
from dataclasses import dataclass

from app.extensions import db
from app.modules.system_admin.models import Lookup


@dataclass
class LookupDef:
    lookup_type: str
    code: str
    description: str
    sort_order: int = 0


class LookupRegistry:
    """Declare lookup seed data in code; synced to DB at startup."""
    def __init__(self):
        self._defs: list[LookupDef] = []

    def register(self, lookup_type: str, code: str, description: str,
                 sort_order: int = 0):
        self._defs.append(LookupDef(lookup_type, code, description, sort_order))

    @property
    def definitions(self):
        return list(self._defs)


# Global registry
registry = LookupRegistry()


def sync_lookups(reg: LookupRegistry | None = None) -> None:
    """Upsert code-registered lookups into the DB (idempotent)."""
    reg = reg or registry
    for d in reg.definitions:
        existing = Lookup.query.filter_by(
            lookup_type=d.lookup_type, code=d.code).first()
        if existing is None:
            db.session.add(Lookup(
                lookup_type=d.lookup_type, code=d.code,
                description=d.description, sort_order=d.sort_order))
    db.session.flush()


class LookupService:
    def find(self, lookup_type: str, code: str) -> Lookup | None:
        """The single row for an exact (lookup_type, code) pair, or None.

        For resolving a value already stored on another record (e.g.
        Vehicle.vehicle_body_type) back to its Lookup row -- to read a
        property of the code itself, such as its picture -- rather than
        listing options for a dropdown (get_by_type/_with_fallback)."""
        if not lookup_type or not code:
            return None
        return Lookup.query.filter_by(lookup_type=lookup_type,
                                      code=code).first()

    def picture_url(self, lookup_type: str, code: str) -> str | None:
        """The public URL for this code's picture, or None.

        Centralises the Lookup -> URL resolution (same shape as
        CompanyProfile's logo_url: /lookups/<id>/picture?v=<attachment
        id> for cache-busting) so every print payload that needs a
        lookup-driven diagram -- currently the vehicle print-out and
        the Handover Checklist, both keyed off vehicle_body_type --
        builds the same URL the same way rather than each re-deriving
        it and risking drift.
        """
        if not code:
            return None
        row = self.find(lookup_type, code)
        if row is not None and getattr(row, "picture_attachment_id", None):
            return f"/api/v1/lookups/{row.id}/picture?v={row.picture_attachment_id}"
        return None

    def get_by_type(self, lookup_type: str) -> list:
        return (Lookup.query
                .filter_by(lookup_type=lookup_type, is_active=True)
                .order_by(Lookup.sort_order, Lookup.code)
                .all())

    def get_by_type_with_fallback(self, lookup_type: str) -> list:
        """Like get_by_type, but falls back to the code-registered defaults
        (module-level registry.register() calls) if the DB hasn't been
        seeded yet (`flask seed all`) — so dropdowns never render empty on
        a fresh install."""
        rows = self.get_by_type(lookup_type)
        if rows:
            return rows
        from types import SimpleNamespace
        return [SimpleNamespace(code=d.code, description=d.description)
               for d in registry.definitions if d.lookup_type == lookup_type]

    def create(self, lookup_type: str, code: str, description: str,
               sort_order: int = 0) -> Lookup:
        item = Lookup(lookup_type=lookup_type, code=code,
                      description=description, sort_order=sort_order)
        db.session.add(item)
        db.session.commit()
        return item

    def update(self, lookup_id: int, **kwargs) -> Lookup | None:
        item = db.session.get(Lookup, lookup_id)
        if item is None:
            return None
        for k, v in kwargs.items():
            setattr(item, k, v)
        db.session.commit()
        return item

    def deactivate(self, lookup_id: int) -> None:
        item = db.session.get(Lookup, lookup_id)
        if item:
            item.is_active = False
            db.session.commit()
