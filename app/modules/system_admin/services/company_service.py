"""Company Profile service — singleton row management."""
from app.extensions import db
from app.modules.system_admin.models import CompanyProfile


class SingletonError(Exception):
    pass


class CompanyProfileService:
    def get(self) -> CompanyProfile | None:
        return CompanyProfile.query.filter_by(is_active=True).first()

    def save(self, **kwargs) -> CompanyProfile:
        profile = self.get()
        if profile is None:
            profile = CompanyProfile(**kwargs)
            db.session.add(profile)
        else:
            for k, v in kwargs.items():
                setattr(profile, k, v)
        db.session.commit()
        return profile
