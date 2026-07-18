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
    _seed_transaction_types()
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


def _seed_transaction_types() -> None:
    """Default Transaction Types for the MO Category/Transaction Type
    enhancement — admin-configurable from here on (System Admin can add
    more later), but these are the ones named explicitly in the spec.
    'group' only organizes the New MO form's dropdown into optgroups; the
    real Category (used for validation) is order_category."""
    from app.modules.transactions.maintenance_order.models import TransactionType

    defaults = [
        # (code, name, order_category, group)
        ("MAINT-SERVICING", "Servicing", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REPAIR", "Repair", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-TROUBLESHOOT", "Troubleshooting", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-TOWING", "Road Call / Towing", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-OVERHAUL", "Overhauling", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-INSPECTION", "Inspection", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-EMISSION", "Emission Test", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REPL-UNIT", "Replacement Unit", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REPAINT", "Repainting", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REHAB", "Rehabilitation", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REWIND", "Rewinding", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-FABRICATION", "Fabrication", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-INSTALLATION", "Installation", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-UPGRADE", "Upgrading", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-WASHING", "Washing / Detailing", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-ADJUSTMENT", "Adjustment / Alignment", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-ACCIDENT-MINOR", "Accident Minor", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-ACCIDENT-MAJOR", "Accident Major", "MAINTENANCE", "MAINTENANCE"),

        ("DEP-ASSIGNMENT", "Assignment", "OPERATIONAL", "DEPLOYMENT"),
        ("DEP-REASSIGNMENT", "Reassignment", "OPERATIONAL", "DEPLOYMENT"),
        ("DEP-RELOCATION", "Relocation", "OPERATIONAL", "DEPLOYMENT"),
        ("DEP-TRANSFER", "Transfer / Relocation", "OPERATIONAL", "DEPLOYMENT"),

        ("ADM-REG-AMEND", "Registration Amendments", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-OWNER", "Change of Ownership", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-COLOR", "Change Color", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-ENGINE", "Change Engine", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-CHASSIS", "Change Chassis", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-BODY", "Change Body Type", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-MORTGAGE", "Mortgage / Annotation", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CANCEL-MORTGAGE", "Cancellation of Mortgage", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-TRAFFIC-VIOLATION", "Traffic Violation / Apprehension", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-TRAINING", "Training / Seminar", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-MIGRATE-EXPENSE", "Migration of Repair Expenses", "OPERATIONAL", "ADMINISTRATIVE"),

        ("DIS-SCRAPPAGE", "Scrappage", "OPERATIONAL", "DISPOSAL"),
        ("DIS-CARNAPPED", "Carnapped", "OPERATIONAL", "DISPOSAL"),
        ("DIS-TOTAL-LOSS", "Total Loss / Wreck", "OPERATIONAL", "DISPOSAL"),
        ("DIS-UNECONOMICAL", "Uneconomical to Repair", "OPERATIONAL", "DISPOSAL"),

        ("ACC-APPLICATION", "Application", "OPERATIONAL", "ACCESSORIES"),
        ("ACC-INSTALLATION", "Installation", "OPERATIONAL", "ACCESSORIES"),
        ("ACC-FABRICATION", "Fabrication", "OPERATIONAL", "ACCESSORIES"),
        ("ACC-REPLACEMENT", "Replacement", "OPERATIONAL", "ACCESSORIES"),
    ]
    for i, (code, name, order_category, group) in enumerate(defaults):
        if not TransactionType.query.filter_by(code=code).first():
            db.session.add(TransactionType(
                code=code, name=name, order_category=order_category,
                group=group, sort_order=i))
    db.session.commit()


pm_cli = AppGroup("pm", help="Preventive Maintenance testing/ops commands.")


@pm_cli.command("run-due-check")
def pm_run_due_check():
    """Manually run the same due/overdue scan Celery beat would run daily
    — fires notifications and (only for AUTO_MO-policy templates) creates
    Maintenance Orders. Safe to run repeatedly; idempotent."""
    from app.modules.transactions.maintenance_order.tasks import (
        auto_generate_due_maintenance_orders)
    created = auto_generate_due_maintenance_orders()
    click.echo(f"Due/overdue scan complete. Maintenance Orders created: {created}")


registration_cli = AppGroup("registration",
                            help="Vehicle Registration renewal testing/ops commands.")


@registration_cli.command("run-due-check")
def registration_run_due_check():
    """Manually run the same registration-renewal due/overdue scan
    Celery beat would run daily — fires notifications and (only for
    AUTO_REGISTRATION-policy templates) creates DRAFT renewal
    registrations. Safe to run repeatedly; idempotent."""
    from app.modules.transactions.vehicle_registration.tasks import (
        auto_generate_due_registrations)
    created = auto_generate_due_registrations()
    click.echo(f"Due/overdue scan complete. Registrations created: {created}")


def register_cli(app):
    app.cli.add_command(seed_cli)
    app.cli.add_command(pm_cli)
    app.cli.add_command(registration_cli)
