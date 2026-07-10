from app.extensions import db
from app.core.models.base import BaseModel
from app.core.repository.base_repository import BaseRepository


class Gadget(db.Model, BaseModel):
    __tablename__ = "test_gadget"
    name = db.Column(db.String(50))


class GadgetRepository(BaseRepository):
    model = Gadget


def test_create_and_get(db):
    repo = GadgetRepository()
    g = repo.create(name="g1")
    db.session.commit()
    assert repo.get_by_id(g.id).name == "g1"


def test_list_excludes_soft_deleted(db):
    repo = GadgetRepository()
    g1 = repo.create(name="a")
    g2 = repo.create(name="b")
    db.session.commit()
    repo.soft_delete(g2.id)
    db.session.commit()
    names = [g.name for g in repo.list()]
    assert names == ["a"]


def test_update(db):
    repo = GadgetRepository()
    g = repo.create(name="old")
    db.session.commit()
    repo.update(g.id, name="new")
    db.session.commit()
    assert repo.get_by_id(g.id).name == "new"


def test_list_with_filters(db):
    repo = GadgetRepository()
    repo.create(name="x")
    repo.create(name="y")
    db.session.commit()
    assert [g.name for g in repo.list(name="y")] == ["y"]
