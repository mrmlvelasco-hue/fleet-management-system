from datetime import datetime

from app.extensions import db
from app.core.models.base import BaseModel


class Widget(db.Model, BaseModel):
    """Throwaway model used only for exercising the mixin."""
    __tablename__ = "test_widget"
    name = db.Column(db.String(50))


def test_base_model_columns(db):
    w = Widget(name="a")
    db.session.add(w)
    db.session.commit()
    assert w.id is not None
    assert isinstance(w.created_at, datetime)
    assert w.is_active is True


def test_soft_delete_flag(db):
    w = Widget(name="b")
    db.session.add(w)
    db.session.commit()
    w.is_active = False
    db.session.commit()
    assert Widget.query.filter_by(is_active=True).count() == 0
