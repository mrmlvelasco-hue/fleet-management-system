"""Every permission an API endpoint requires must actually exist.

Reported by the client: an admin account with full access still got
"This account lacks the 'maintenanceorder.submit' permission" when
submitting a Maintenance Order for approval, and asked whether
re-seeding would fix it.

It would not have. app/modules/transactions/routes.py registers
exactly five actions per module:

    for _act in ["view", "create", "update", "delete", "print"]

There is no "submit", "complete" or "cancel" action, so those
permission codes cannot be granted to ANY role, by any seed, ever --
no row exists to grant. Flask's own routes gate all three on
`.update`. Five API endpoints had invented codes outside that set,
making each one permanently unusable rather than merely
under-permissioned.

This test guards the whole class rather than the five instances:
anything requiring a code the registry does not register fails here,
including future endpoints.
"""
import re
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2] / "app" / "modules" / "api"


def _registered_codes():
    from app.core.security.registry import PermissionRegistry, registry
    from app.core.security.registry import sync_permissions  # noqa: F401
    # Import the route modules for their registration side effects.
    import app.modules.transactions.routes  # noqa: F401
    import app.modules.master_data.routes  # noqa: F401
    import app.modules.user_management.routes  # noqa: F401
    import app.modules.document_config.routes  # noqa: F401
    import app.modules.registration_config.routes  # noqa: F401
    assert isinstance(registry, PermissionRegistry)
    return {d.code for d in registry.definitions}


def test_every_api_permission_code_is_actually_registered(app):
    codes = _registered_codes()
    offenders = []
    for path in sorted(API_DIR.glob("*.py")):
        for line_no, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            m = re.search(r'api_auth_required\(\s*"([a-z_]+\.[a-z_]+)"', line)
            if m and m.group(1) not in codes:
                offenders.append(f"{path.name}:{line_no} -> {m.group(1)}")
    assert not offenders, (
        "These endpoints require permission codes that are never "
        "registered, so no role can ever hold them and the endpoint is "
        "permanently unusable:\n  " + "\n  ".join(offenders))
