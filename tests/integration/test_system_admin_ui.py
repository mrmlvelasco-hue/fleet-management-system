from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.system_admin.models import SystemParameter


def _login(client, db, *, permission_codes=()):
    role = Role(name="SysAdminRole")
    for code in permission_codes:
        module, action = code.split(".")
        perm = Permission(code=code, module=module, action=action)
        db.session.add(perm)
        role.permissions.append(perm)
    user = User(username="kara", email="k@example.com",
                password_hash=hash_password("pw123456"))
    user.roles.append(role)
    db.session.add_all([role, user])
    db.session.commit()
    client.post("/login", data={"username": "kara", "password": "pw123456"})
    return user


def test_sysparam_403_without_permission(client, db):
    _login(client, db)
    assert client.get("/admin/system-parameters").status_code == 403


def test_sysparam_200_with_permission(client, db):
    _login(client, db, permission_codes=["sysparam.view"])
    assert client.get("/admin/system-parameters").status_code == 200


def test_lookup_pages_render(client, db):
    _login(client, db, permission_codes=["lookup.view", "lookup.create"])
    assert client.get("/admin/lookups").status_code == 200
    assert client.get("/admin/lookups/new").status_code == 200


def test_company_profile_page_renders(client, db):
    _login(client, db, permission_codes=["company.view"])
    assert client.get("/admin/company-profile").status_code == 200


def test_email_template_pages_render(client, db):
    _login(client, db, permission_codes=[
        "emailtemplate.view", "emailtemplate.create"])
    assert client.get("/admin/email-templates").status_code == 200
    assert client.get("/admin/email-templates/new").status_code == 200


def test_notification_rule_pages_render(client, db):
    _login(client, db, permission_codes=[
        "notificationrule.view", "notificationrule.create"])
    assert client.get("/admin/notification-rules").status_code == 200
    assert client.get("/admin/notification-rules/new").status_code == 200


def test_audit_trail_page_renders(client, db):
    _login(client, db, permission_codes=["audittrail.view"])
    assert client.get("/admin/audit-trail").status_code == 200


def test_unread_count_endpoint(client, db):
    _login(client, db)
    resp = client.get("/admin/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 0


def test_seed_all_includes_system_parameters(app, db):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["seed", "all", "--admin-password", "Admin123!"])
    assert result.exit_code == 0, result.output
    assert SystemParameter.query.filter_by(
        code="SESSION_TIMEOUT_MINUTES").count() == 1


def test_end_to_end_approval_creates_notification(app, db):
    """Submit approval → notification rule fires → InAppNotification created."""
    from app.modules.system_admin.models import (
        NotificationRule, InAppNotification)
    from app.modules.document_config.service import (
        DocumentTypeService, NumberingSchemeService)
    from app.modules.approval_config.service import (
        ApprovalPathService, ApprovalMatrixService)
    from app.core.approval.engine import ApprovalEngine
    from app.modules.user_management.models import User, Role

    role = Role(name="Sup")
    submitter = User(username="sub2", email="s2@x.com", password_hash="x")
    db.session.add_all([role, submitter])
    db.session.commit()

    dt = DocumentTypeService().create(
        code="TT2", name="Trip Ticket 2", requires_approval=True)
    path = ApprovalPathService().create(name="Solo2", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
    ApprovalMatrixService().create(dt.id, path.id)

    db.session.add(NotificationRule(
        event_code="submitted", channel="IN_APP",
        recipient_type="SUBMITTER"))
    db.session.commit()

    eng = ApprovalEngine()
    eng.submit("TT2", "trip_tickets", 1, user=submitter)

    assert InAppNotification.query.filter_by(
        user_id=submitter.id, event_code="submitted").count() == 1
