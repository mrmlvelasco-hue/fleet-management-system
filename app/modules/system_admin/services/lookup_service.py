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
    def get_by_type(self, lookup_type: str) -> list:
        return (Lookup.query
                .filter_by(lookup_type=lookup_type, is_active=True)
                .order_by(Lookup.sort_order, Lookup.code)
                .all())

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
