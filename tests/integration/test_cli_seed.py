from app.modules.user_management.models import User, Role, Permission


def test_seed_all_creates_admin_with_all_permissions(app, db):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["seed", "all", "--admin-password", "Admin123!"])
    assert result.exit_code == 0, result.output
    admin = User.query.filter_by(username="admin").first()
    assert admin is not None
    role = Role.query.filter_by(name="System Administrator").first()
    assert role.is_system_role is True
    assert Permission.query.count() > 0
    assert admin.has_permission("user.create")


def test_seed_all_is_idempotent(app, db):
    runner = app.test_cli_runner()
    runner.invoke(args=["seed", "all", "--admin-password", "Admin123!"])
    result = runner.invoke(args=["seed", "all", "--admin-password", "Admin123!"])
    assert result.exit_code == 0
    assert User.query.filter_by(username="admin").count() == 1
