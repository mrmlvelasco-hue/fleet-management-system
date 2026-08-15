"""Sidebar appearance ("skin") — registry and resolution.

A skin only changes how the sidebar looks; it never changes what a
person can see or do. It is nonetheless resolved through the same
two-level pattern the rest of the app uses for configurable behaviour:

    System Parameter  SIDEBAR_SKIN_DEFAULT   (company-wide house style)
        ↓  overridden by
    users.sidebar_skin                        (this person's choice)

Stored on the user rather than in a cookie deliberately. A cookie is
per-device and per-browser, so someone who picks a skin at their desk
would find the default again on their phone and after any cache clear
-- a preference that silently forgets itself reads as broken. The
column costs one migration and the choice then follows the account.

The resolved value is rendered into the page by the SERVER (see the
context processor in the app factory), not applied by JavaScript after
paint, so there is no flash of the wrong sidebar on every page load.

Nothing here raises during resolution. A skin code can go stale in two
realistic ways -- a skin retired in a later release leaving rows behind
that point at it, or a hand-edited System Parameter, since that screen
is a free-text field. Either way the answer is the Classic sidebar, not
a code the stylesheet has no rules for.
"""
from dataclasses import dataclass

DEFAULT_SKIN = "classic"

#: The System Parameter holding the company-wide default.
DEFAULT_SKIN_PARAMETER = "SIDEBAR_SKIN_DEFAULT"


class InvalidSkinError(ValueError):
    """Raised when something tries to SAVE a skin code that doesn't
    exist. Deliberately distinct from resolution, which never raises:
    writing a bad value is a caller bug worth surfacing, whereas
    reading one back later is a data-drift case that must degrade
    quietly."""


@dataclass(frozen=True)
class Skin:
    code: str
    label: str
    description: str
    #: Whether this skin's own surface is dark. The picker uses it to
    #: render swatches, and the stylesheet uses it to decide which
    #: skins need a dark-mode variant at all -- declared here rather
    #: than inferred from the code name, which would break the moment
    #: someone adds a dark skin not called "dark".
    is_dark: bool


#: Order is the order the picker shows them in: the current default
#: first, then the recommended alternative, then the lighter options.
SKINS = (
    Skin("classic", "Classic",
         "Ocean Depths navy", is_dark=True),
    Skin("soft-dark", "Soft Dark",
         "Navy with soft depth", is_dark=True),
    Skin("soft-light", "Soft Light",
         "Light neumorphic, teal", is_dark=False),
    Skin("soft-crimson", "Soft Crimson",
         "Light neumorphic, crimson", is_dark=False),
)

_BY_CODE = {s.code: s for s in SKINS}


class SidebarSkinService:

    def list_skins(self):
        """Every skin the picker should offer, in display order."""
        return list(SKINS)

    def get(self, code):
        return _BY_CODE.get(code)

    def is_valid(self, code) -> bool:
        return code in _BY_CODE

    def company_default(self) -> str:
        """The house style set under System Administration.

        Swallows every failure path -- missing parameter, invalid value,
        or the parameters table not existing yet during an early
        migration -- because a sidebar must still render.
        """
        try:
            from app.modules.system_admin.services.system_parameter_service \
                import SystemParameterService
            raw = SystemParameterService().get(DEFAULT_SKIN_PARAMETER)
        except Exception:
            return DEFAULT_SKIN
        code = str(raw).strip() if raw is not None else ""
        return code if self.is_valid(code) else DEFAULT_SKIN

    def resolve(self, user) -> str:
        """The skin to render for `user`.

        `user` may be None (the login page renders the shell before
        anyone is authenticated) or an anonymous user.
        """
        code = getattr(user, "sidebar_skin", None) if user is not None else None
        if self.is_valid(code):
            return code
        return self.company_default()

    def set_for_user(self, user, code) -> str:
        """Save this person's choice. Passing None clears it, returning
        them to whatever the company default currently is -- which is
        not the same as pinning them to Classic, since the default may
        change later."""
        from app.extensions import db
        if code is not None and not self.is_valid(code):
            raise InvalidSkinError(f"Unknown sidebar skin: {code!r}")
        user.sidebar_skin = code
        db.session.commit()
        return self.resolve(user)
