"""PermissionRegistry: modules declare their permission codes in code;
sync_permissions() upserts them into the DB at startup so DB permissions
never drift from what the code enforces."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    code: str
    module: str
    action: str
    description: str = ""


class PermissionRegistry:
    def __init__(self):
        self._defs: dict[str, PermissionDef] = {}

    def register(self, code: str, module: str, action: str, description: str = ""):
        self._defs[code] = PermissionDef(code, module, action, description)

    @property
    def definitions(self) -> list[PermissionDef]:
        return list(self._defs.values())


# Global registry instance modules import and register against.
registry = PermissionRegistry()


def sync_permissions(reg: "PermissionRegistry | None" = None) -> None:
    from app.extensions import db
    from app.modules.user_management.models import Permission

    reg = reg or registry
    existing = {p.code for p in Permission.query.all()}
    for d in reg.definitions:
        if d.code not in existing:
            db.session.add(Permission(code=d.code, module=d.module,
                                      action=d.action, description=d.description))
    db.session.flush()
