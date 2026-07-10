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
    """Sync permissions then create the admin role/user."""
    sync_permissions()
    db.session.commit()
    _seed_admin(admin_password)


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


def register_cli(app):
    app.cli.add_command(seed_cli)
