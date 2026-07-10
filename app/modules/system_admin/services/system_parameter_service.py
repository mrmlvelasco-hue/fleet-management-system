"""System Parameter service — typed get/set for configurable business rules."""
from decimal import Decimal

from app.extensions import db
from app.modules.system_admin.models import SystemParameter


class SystemParameterService:
    def get(self, code: str, default=None):
        """Return the parameter value cast to its declared data_type."""
        param = SystemParameter.query.filter_by(
            code=code, is_active=True).first()
        if param is None:
            return default
        return self._cast(param.value, param.data_type)

    def set(self, code: str, value: str) -> None:
        """Update an existing parameter value (string form)."""
        param = SystemParameter.query.filter_by(
            code=code, is_active=True).first()
        if param is None:
            return
        param.value = str(value)
        db.session.commit()

    def get_group(self, group_name: str) -> dict:
        """Return all parameters in a group as {code: typed_value}."""
        params = SystemParameter.query.filter_by(
            group_name=group_name, is_active=True).all()
        return {p.code: self._cast(p.value, p.data_type) for p in params}

    @staticmethod
    def _cast(value: str, data_type: str):
        if data_type == "INTEGER":
            return int(value)
        if data_type == "BOOLEAN":
            return value.lower() in ("true", "1", "yes")
        if data_type == "DECIMAL":
            return Decimal(value)
        return value  # STRING
