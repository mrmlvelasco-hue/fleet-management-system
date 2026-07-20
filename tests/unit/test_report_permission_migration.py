"""Tests for the report-permission migration step in cli.py --
_migrate_report_permissions_from_data_permissions(). Reports moved from
being gated on the underlying data's view permission (vehicle.view, etc.)
to their own dedicated permission (reportvehicleregister.view, etc.) so
an admin can grant/revoke access to a specific report per Role. This
migration step ensures no role silently loses report access on upgrade.
"""
from app.cli import _migrate_report_permissions_from_data_permissions
from app.modules.user_management.models import Role, Permission


def _perm(code, module="x", action="view"):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        p = Permission(code=code, module=module, action=action)
    return p


def test_migration_grants_report_permission_to_roles_with_old_data_permission(db):
    old_perm = _perm("vehicle.view")
    new_perm = _perm("reportvehicleregister.view")
    db.session.add_all([old_perm, new_perm])
    db.session.flush()

    role = Role(name="Branch Manager")
    role.permissions.append(old_perm)
    db.session.add(role)
    db.session.commit()

    _migrate_report_permissions_from_data_permissions()
    db.session.commit()

    db.session.refresh(role)
    assert new_perm in role.permissions


def test_migration_is_idempotent_and_does_not_duplicate(db):
    old_perm = _perm("maintenanceorder.view")
    new_perm = _perm("reportpmscompliance.view")
    db.session.add_all([old_perm, new_perm])
    db.session.flush()

    role = Role(name="Fleet Supervisor")
    role.permissions.append(old_perm)
    db.session.add(role)
    db.session.commit()

    _migrate_report_permissions_from_data_permissions()
    db.session.commit()
    _migrate_report_permissions_from_data_permissions()
    db.session.commit()

    db.session.refresh(role)
    assert role.permissions.count(new_perm) == 1


def test_migration_does_not_grant_to_roles_without_the_old_permission(db):
    old_perm = _perm("vehicleregistration.view")
    new_perm = _perm("reportregistrationexpiry.view")
    db.session.add_all([old_perm, new_perm])
    db.session.flush()

    unrelated_role = Role(name="Warehouse Clerk")  # has neither permission
    db.session.add(unrelated_role)
    db.session.commit()

    _migrate_report_permissions_from_data_permissions()
    db.session.commit()

    db.session.refresh(unrelated_role)
    assert new_perm not in unrelated_role.permissions


def test_admin_can_still_disable_a_specific_report_after_migration(db):
    """The whole point: after the migration grants parity, an admin must
    still be able to uncheck ONE report's permission for a role without
    it being re-granted or affecting the others."""
    old_perm = _perm("maintenanceorder.view")
    pms_perm = _perm("reportpmscompliance.view")
    cost_perm = _perm("reportmaintenancecost.view")
    db.session.add_all([old_perm, pms_perm, cost_perm])
    db.session.flush()

    role = Role(name="Regional Viewer")
    role.permissions.append(old_perm)
    db.session.add(role)
    db.session.commit()

    _migrate_report_permissions_from_data_permissions()
    db.session.commit()
    db.session.refresh(role)
    assert pms_perm in role.permissions
    assert cost_perm in role.permissions

    # Admin explicitly disables just the PMS Compliance report for this role
    role.permissions.remove(pms_perm)
    db.session.commit()

    # Re-running the migration must NOT silently re-grant what the admin
    # just disabled.
    _migrate_report_permissions_from_data_permissions()
    db.session.commit()
    db.session.refresh(role)
    assert pms_perm not in role.permissions
    assert cost_perm in role.permissions
