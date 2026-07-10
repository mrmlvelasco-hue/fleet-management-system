"""Seed commands: `flask seed permissions|admin|all`."""
import click
from flask.cli import AppGroup

from app.extensions import db
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.user_management.models import User, Role, Permission

seed_cli = AppGroup("seed", help="Seed initial data.")


@seed_cli.command("permissions")
def seed_permissions():
    """Sync code-registered permissions into the database."""
    sync_permissions()
    db.session.commit()
    click.echo(f"Permissions synced: {Permission.query.count()} total.")


@seed_cli.command("admin")
@click.option("--admin-password", prompt=True, hide_input=True,
              help="Password for the admin account.")
def seed_admin(admin_password):
    """Create the System Administrator role (all permissions) and admin user."""
    _seed_admin(admin_password)


@seed_cli.command("all")
@click.option("--admin-password", prompt=True, hide_input=True)
def seed_all(admin_password):
    """Sync permissions then create the admin role/user and seed defaults."""
    sync_permissions()
    db.session.commit()
    _seed_admin(admin_password)
    _seed_system_parameters()
    _seed_dashboard_widgets()
    _seed_lookups()
    db.session.commit()
    click.echo("Default system parameters, dashboard widgets and lookups seeded.")


def _seed_admin(admin_password: str) -> None:
    role = Role.query.filter_by(name="System Administrator").first()
    if role is None:
        role = Role(name="System Administrator",
                    description="Full access to all modules",
                    is_system_role=True)
        db.session.add(role)
    role.permissions = Permission.query.all()

    admin = User.query.filter_by(username="admin").first()
    if admin is None:
        admin = User(username="admin", email="admin@example.com",
                     password_hash=hash_password(admin_password),
                     first_name="System", last_name="Administrator",
                     must_change_password=True)
        admin.roles.append(role)
        db.session.add(admin)
        click.echo("Admin user created (username: admin).")
    else:
        click.echo("Admin user already exists; skipped.")
    db.session.commit()


def _seed_system_parameters() -> None:
    from app.modules.system_admin.models import SystemParameter
    defaults = [
        ("SESSION_TIMEOUT_MINUTES", "30", "INTEGER", "SECURITY",
         "Session timeout in minutes"),
        ("MAX_FAILED_LOGIN_ATTEMPTS", "5", "INTEGER", "SECURITY",
         "Max failed login attempts before lockout"),
        ("REQUIRE_DRIVER_FROM_MASTER", "YES", "STRING", "TRIP_TICKET",
         "YES = driver must come from Driver Master; NO = manual entry"),
        ("COMPANY_NAME", "My Company", "STRING", "GENERAL", "Company name"),
    ]
    for code, value, data_type, group, desc in defaults:
        if not SystemParameter.query.filter_by(code=code).first():
            db.session.add(SystemParameter(
                code=code, value=value, data_type=data_type,
                group_name=group, description=desc))
    db.session.flush()


def _seed_dashboard_widgets() -> None:
    from app.modules.system_admin.models import DashboardWidget
    widgets = [
        ("FLEET", "Fleet", "bi-truck", 1),
        ("MAINTENANCE", "Maintenance", "bi-wrench", 2),
        ("APPROVALS", "Approvals", "bi-check2-square", 3),
        ("REGISTRATIONS", "Registrations", "bi-card-checklist", 4),
        ("TIRES", "Tires", "bi-circle", 5),
        ("BATTERIES", "Batteries", "bi-battery-half", 6),
    ]
    for code, label, icon, sort in widgets:
        if not DashboardWidget.query.filter_by(code=code).first():
            db.session.add(DashboardWidget(
                code=code, label=label, icon=icon,
                sort_order=sort, default_visible=True))
    db.session.flush()


def _seed_lookups() -> None:
    """Sync all module-registered lookup types (FUEL_TYPE, LICENSE_TYPE, etc.)
    Must import the master_data routes module first so its lookup
    registrations run (module-level registry.register() calls)."""
    import app.modules.master_data.routes  # noqa: F401 (triggers registration)
    from app.modules.system_admin.services.lookup_service import sync_lookups
    sync_lookups()


def register_cli(app):
    app.cli.add_command(seed_cli)
