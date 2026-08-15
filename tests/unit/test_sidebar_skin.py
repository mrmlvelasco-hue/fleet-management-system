"""Per-user sidebar appearance ("skin").

A skin is purely presentational, but it is resolved through the same
two-level pattern the rest of the app uses for configurable behaviour:
a company-wide default held in System Parameters, which any individual
user may override for their own account.

Resolution must never raise and never return something the CSS has no
rules for -- a stale or hand-edited skin code has to degrade to the
Classic sidebar, not leave someone staring at an unstyled nav.
"""
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def admin(app, db):
    from app.modules.user_management.models import User
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    return User.query.filter_by(username="admin").first()


def _svc():
    from app.core.appearance.skin_service import SidebarSkinService
    return SidebarSkinService()


# ── The registry ────────────────────────────────────────────────────

def test_all_four_skins_are_offered(app, db):
    codes = [s.code for s in _svc().list_skins()]
    assert codes == ["classic", "soft-dark", "soft-light", "soft-crimson"]


def test_every_skin_has_a_label_and_description_for_the_picker(app, db):
    for skin in _svc().list_skins():
        assert skin.label, f"{skin.code} has no label"
        assert skin.description, f"{skin.code} has no description"


def test_classic_is_the_fallback_default(app, db):
    from app.core.appearance.skin_service import DEFAULT_SKIN
    assert DEFAULT_SKIN == "classic"


# ── Company default via System Parameters ───────────────────────────

def test_company_default_comes_from_system_parameters(app, db):
    from app.modules.system_admin.models import SystemParameter
    db.session.add(SystemParameter(
        code="SIDEBAR_SKIN_DEFAULT", value="soft-dark",
        data_type="STRING", group_name="APPEARANCE"))
    db.session.commit()
    assert _svc().company_default() == "soft-dark"


def test_company_default_falls_back_when_parameter_absent(app, db):
    assert _svc().company_default() == "classic"


def test_company_default_falls_back_when_parameter_is_nonsense(app, db):
    """A hand-edited parameter must not break every sidebar in the
    system -- System Parameters is a free-text field, so an invalid
    value here is a realistic operator mistake, not a hypothetical."""
    from app.modules.system_admin.models import SystemParameter
    db.session.add(SystemParameter(
        code="SIDEBAR_SKIN_DEFAULT", value="chartreuse-bubbles",
        data_type="STRING", group_name="APPEARANCE"))
    db.session.commit()
    assert _svc().company_default() == "classic"


# ── Per-user override ───────────────────────────────────────────────

def test_user_with_no_preference_gets_the_company_default(app, db, admin):
    from app.modules.system_admin.models import SystemParameter
    db.session.add(SystemParameter(
        code="SIDEBAR_SKIN_DEFAULT", value="soft-light",
        data_type="STRING", group_name="APPEARANCE"))
    db.session.commit()
    assert admin.sidebar_skin is None
    assert _svc().resolve(admin) == "soft-light"


def test_user_preference_overrides_the_company_default(app, db, admin):
    from app.modules.system_admin.models import SystemParameter
    db.session.add(SystemParameter(
        code="SIDEBAR_SKIN_DEFAULT", value="soft-light",
        data_type="STRING", group_name="APPEARANCE"))
    db.session.commit()
    _svc().set_for_user(admin, "soft-crimson")
    assert _svc().resolve(admin) == "soft-crimson"


def test_setting_a_skin_persists_it_on_the_user(app, db, admin):
    from app.modules.user_management.models import User
    _svc().set_for_user(admin, "soft-dark")
    db.session.expire_all()
    assert User.query.filter_by(username="admin").first().sidebar_skin \
        == "soft-dark"


def test_setting_an_unknown_skin_is_rejected(app, db, admin):
    from app.core.appearance.skin_service import InvalidSkinError
    with pytest.raises(InvalidSkinError):
        _svc().set_for_user(admin, "not-a-real-skin")
    assert admin.sidebar_skin is None


def test_a_stale_skin_stored_on_the_user_degrades_to_classic(app, db, admin):
    """A skin removed in a later release leaves rows behind pointing at
    it. Those users must land on Classic rather than on a code with no
    CSS behind it."""
    admin.sidebar_skin = "skin-that-was-retired"
    db.session.commit()
    assert _svc().resolve(admin) == "classic"


def test_resolve_handles_an_anonymous_user(app, db):
    """The login page renders the shell before anyone is authenticated."""
    assert _svc().resolve(None) == "classic"


def test_clearing_a_users_preference_returns_them_to_the_default(
        app, db, admin):
    _svc().set_for_user(admin, "soft-dark")
    _svc().set_for_user(admin, None)
    assert admin.sidebar_skin is None
    assert _svc().resolve(admin) == "classic"


# ── Dark mode pairing ───────────────────────────────────────────────

def test_every_skin_declares_whether_its_surface_is_dark(app, db):
    """The picker shows a swatch per skin, and the shell needs to know
    which skins are already dark so the dark-mode variant can be paired
    correctly rather than guessed from the code name."""
    by_code = {s.code: s for s in _svc().list_skins()}
    assert by_code["classic"].is_dark is True
    assert by_code["soft-dark"].is_dark is True
    assert by_code["soft-light"].is_dark is False
    assert by_code["soft-crimson"].is_dark is False
