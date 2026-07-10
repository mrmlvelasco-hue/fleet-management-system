import pytest
from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)
from app.modules.system_admin.models import SystemParameter


def test_get_string_parameter(db):
    db.session.add(SystemParameter(
        code="COMPANY_NAME", value="FMS Corp",
        data_type="STRING", description="Company name", group_name="GENERAL"))
    db.session.commit()
    svc = SystemParameterService()
    assert svc.get("COMPANY_NAME") == "FMS Corp"


def test_get_boolean_parameter(db):
    db.session.add(SystemParameter(
        code="REQUIRE_DRIVER_FROM_MASTER", value="true",
        data_type="BOOLEAN", group_name="TRIP_TICKET"))
    db.session.commit()
    assert SystemParameterService().get("REQUIRE_DRIVER_FROM_MASTER") is True


def test_get_integer_parameter(db):
    db.session.add(SystemParameter(
        code="SESSION_TIMEOUT_MINUTES", value="30",
        data_type="INTEGER", group_name="SECURITY"))
    db.session.commit()
    assert SystemParameterService().get("SESSION_TIMEOUT_MINUTES") == 30


def test_get_missing_returns_default(db):
    assert SystemParameterService().get("NONEXISTENT", default="fallback") == "fallback"


def test_set_updates_value(db):
    db.session.add(SystemParameter(
        code="MAX_FAILED_LOGIN_ATTEMPTS", value="5",
        data_type="INTEGER", group_name="SECURITY", is_editable=True))
    db.session.commit()
    SystemParameterService().set("MAX_FAILED_LOGIN_ATTEMPTS", "10")
    assert SystemParameterService().get("MAX_FAILED_LOGIN_ATTEMPTS") == 10


def test_get_group_returns_dict(db):
    db.session.add(SystemParameter(
        code="A", value="1", data_type="INTEGER", group_name="GRP"))
    db.session.add(SystemParameter(
        code="B", value="2", data_type="INTEGER", group_name="GRP"))
    db.session.commit()
    result = SystemParameterService().get_group("GRP")
    assert result == {"A": 1, "B": 2}
