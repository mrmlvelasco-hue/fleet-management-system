"""Services for organisational master data."""
from app.extensions import db
from app.modules.master_data.org.models import Branch, Department, BusinessUnit


class DuplicateCodeError(Exception):
    pass


class _BaseMasterService:
    model = None

    def get(self, record_id, include_inactive=True):
        obj = db.session.get(self.model, record_id)
        if obj is None or (not include_inactive and not obj.is_active):
            return None
        return obj

    def list(self, include_inactive=False):
        q = self.model.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.order_by(self.model.id).all()

    def deactivate(self, record_id):
        obj = db.session.get(self.model, record_id)
        if obj:
            obj.is_active = False
            db.session.commit()

    def reactivate(self, record_id):
        obj = db.session.get(self.model, record_id)
        if obj:
            obj.is_active = True
            db.session.commit()


class BranchService(_BaseMasterService):
    model = Branch

    def create(self, code, name, **kwargs):
        if Branch.query.filter_by(code=code).first():
            raise DuplicateCodeError(f"Branch code '{code}' already exists.")
        obj = Branch(code=code, name=name, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(Branch, record_id)
        if obj is None:
            return None
        for k, v in kwargs.items():
            setattr(obj, k, v)
        db.session.commit()
        return obj


class DepartmentService(_BaseMasterService):
    model = Department

    def create(self, code, name, branch_id, **kwargs):
        if Department.query.filter_by(
                code=code, branch_id=branch_id).first():
            raise DuplicateCodeError(
                f"Department code '{code}' already exists in this branch.")
        obj = Department(code=code, name=name, branch_id=branch_id, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(Department, record_id)
        if obj is None:
            return None
        for k, v in kwargs.items():
            setattr(obj, k, v)
        db.session.commit()
        return obj


class BusinessUnitService(_BaseMasterService):
    model = BusinessUnit

    def create(self, code, name, **kwargs):
        if BusinessUnit.query.filter_by(code=code).first():
            raise DuplicateCodeError(
                f"Business unit code '{code}' already exists.")
        obj = BusinessUnit(code=code, name=name, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(BusinessUnit, record_id)
        if obj is None:
            return None
        for k, v in kwargs.items():
            setattr(obj, k, v)
        db.session.commit()
        return obj
