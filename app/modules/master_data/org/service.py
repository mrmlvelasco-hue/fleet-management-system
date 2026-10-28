"""Services for organisational master data."""
from app.extensions import db
from app.modules.master_data.org.models import (
    Branch, Department, BusinessUnit, Region, AreaDistrict, OrgCompany)


class DuplicateCodeError(Exception):
    pass


class InvalidOrgDataError(Exception):
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


class RegionService(_BaseMasterService):
    model = Region

    def create(self, code, name, **kwargs):
        if Region.query.filter_by(code=code).first():
            raise DuplicateCodeError(f"Region code '{code}' already exists.")
        obj = Region(code=code, name=name, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(Region, record_id)
        if obj is None:
            return None
        for k, v in kwargs.items():
            setattr(obj, k, v)
        db.session.commit()
        return obj


class AreaDistrictService(_BaseMasterService):
    model = AreaDistrict

    def create(self, code, name, region_id, **kwargs):
        if not region_id:
            raise InvalidOrgDataError(
                "An Area/District must belong to a Region.")
        if db.session.get(Region, region_id) is None:
            raise InvalidOrgDataError("That Region does not exist.")
        if AreaDistrict.query.filter_by(
                code=code, region_id=region_id).first():
            raise DuplicateCodeError(
                f"Area/District code '{code}' already exists in this "
                f"Region.")
        obj = AreaDistrict(code=code, name=name, region_id=region_id,
                           **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(AreaDistrict, record_id)
        if obj is None:
            return None
        for k, v in kwargs.items():
            setattr(obj, k, v)
        db.session.commit()
        return obj

    def list_for_region(self, region_id, include_inactive=False):
        q = AreaDistrict.query.filter_by(region_id=region_id)
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.order_by(AreaDistrict.id).all()


class OrgCompanyService(_BaseMasterService):
    model = OrgCompany

    def create(self, short_name, region_id, area_district_id,
              company_name=None, legacy_branch_id=None, **kwargs):
        """D2: the SAME short_name is allowed to repeat under a
        different Region + Area/District (Michael's own example:
        "Excellence" in Luzon/San Mateo vs Visayas/Bacolod) -- the
        uniqueness key is (short_name, region, area), never
        short_name alone."""
        if not short_name or not short_name.strip():
            raise InvalidOrgDataError("short_name is required.")
        if not region_id or not area_district_id:
            raise InvalidOrgDataError(
                "A Company must have both a Region and an Area/District.")
        region = db.session.get(Region, region_id)
        if region is None:
            raise InvalidOrgDataError("That Region does not exist.")
        area = db.session.get(AreaDistrict, area_district_id)
        if area is None:
            raise InvalidOrgDataError("That Area/District does not exist.")
        if area.region_id != region_id:
            raise InvalidOrgDataError(
                f"'{area.name}' belongs to a different Region than the "
                f"one given.")
        if OrgCompany.query.filter_by(
                short_name=short_name, region_id=region_id,
                area_district_id=area_district_id).first():
            raise DuplicateCodeError(
                f"A Company named '{short_name}' already exists in this "
                f"Region and Area/District.")
        obj = OrgCompany(short_name=short_name, company_name=company_name,
                         region_id=region_id,
                         area_district_id=area_district_id,
                         legacy_branch_id=legacy_branch_id, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(OrgCompany, record_id)
        if obj is None:
            return None
        for k, v in kwargs.items():
            setattr(obj, k, v)
        db.session.commit()
        return obj
