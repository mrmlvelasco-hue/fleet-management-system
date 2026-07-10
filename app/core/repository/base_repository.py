"""Generic repository base class (Repository Pattern).

Subclasses set `model`. Methods flush (so ids are assigned) but do NOT
commit; the owning service commits the unit of work.
"""
from app.extensions import db


class BaseRepository:
    model = None  # subclasses must set

    def get_by_id(self, record_id: int, include_inactive: bool = False):
        obj = db.session.get(self.model, record_id)
        if obj is None:
            return None
        if not include_inactive and not obj.is_active:
            return None
        return obj

    def list(self, include_inactive: bool = False, **filters):
        query = db.session.query(self.model)
        if not include_inactive:
            query = query.filter(self.model.is_active.is_(True))
        for attr, value in filters.items():
            query = query.filter(getattr(self.model, attr) == value)
        return query.order_by(self.model.id).all()

    def create(self, **kwargs):
        obj = self.model(**kwargs)
        db.session.add(obj)
        db.session.flush()
        return obj

    def update(self, record_id: int, **kwargs):
        obj = self.get_by_id(record_id)
        if obj is None:
            return None
        for attr, value in kwargs.items():
            setattr(obj, attr, value)
        db.session.flush()
        return obj

    def soft_delete(self, record_id: int):
        obj = self.get_by_id(record_id)
        if obj is None:
            return None
        obj.is_active = False
        db.session.flush()
        return obj
