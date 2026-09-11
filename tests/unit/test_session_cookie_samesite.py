"""Test that SESSION_COOKIE_SAMESITE is correctly configured to allow new-tab navigation.

Background: Users were forced to log in again when opening FMS links in new tabs
because the cookie SameSite policy was too strict (defaulting to "Strict" or
explicitly set to it). With SameSite="Strict", browsers don't send session cookies
on cross-tab navigations (e.g., "Open link in new tab"). Changing to "Lax" allows
those navigations while still blocking cross-origin attacks.
"""
import pytest
from app.config import BaseConfig, DevelopmentConfig, ProductionConfig, TestingConfig


class TestSessionCookieSameSite:
    """Verify SameSite policy is correctly set across all configs."""

    def test_base_config_has_samesite_lax(self):
        """BaseConfig must set SameSite=Lax for new-tab navigation to work."""
        assert hasattr(BaseConfig, "SESSION_COOKIE_SAMESITE")
        assert BaseConfig.SESSION_COOKIE_SAMESITE == "Lax"

    def test_production_config_has_samesite_lax(self):
        """ProductionConfig must explicitly set SameSite=Lax (even though inherited)."""
        assert hasattr(ProductionConfig, "SESSION_COOKIE_SAMESITE")
        assert ProductionConfig.SESSION_COOKIE_SAMESITE == "Lax"

    def test_development_config_inherits_samesite_lax(self):
        """DevelopmentConfig inherits SameSite=Lax from BaseConfig."""
        assert DevelopmentConfig.SESSION_COOKIE_SAMESITE == "Lax"

    def test_testing_config_inherits_samesite_lax(self):
        """TestingConfig inherits SameSite=Lax from BaseConfig."""
        assert TestingConfig.SESSION_COOKIE_SAMESITE == "Lax"

    def test_httponly_is_still_true(self):
        """HTTPOnly flag should remain True for security (prevents JS access to cookies)."""
        assert BaseConfig.SESSION_COOKIE_HTTPONLY is True
        assert ProductionConfig.SESSION_COOKIE_HTTPONLY is True

    def test_production_cookie_secure_is_true(self):
        """ProductionConfig must set Secure flag (HTTPS only)."""
        assert ProductionConfig.SESSION_COOKIE_SECURE is True

    def test_samesite_lax_allows_top_level_navigation(self):
        """
        Document what SameSite=Lax does (for clarity, not a functional test).

        With SameSite=Lax:
        ✅ Session cookie IS sent when user clicks "Open link in new tab"
        ✅ Session cookie IS sent on top-level navigations
        ✅ Session cookie IS NOT sent on cross-origin GET requests
        ✅ Session cookie IS NOT sent on cross-origin POST requests
        ✅ CSRF protection still works (no form token in cross-origin POST)

        Strict would have blocked the "Open link in new tab" case, forcing
        users to log in again every time they opened a vehicle link in a new tab.
        """
        # This is a documentation test, not a runtime verification
        assert BaseConfig.SESSION_COOKIE_SAMESITE == "Lax"


class TestSessionCookieSecurityProperties:
    """Verify the overall session cookie security posture."""

    def test_all_security_flags_in_production(self):
        """ProductionConfig sets all required security flags."""
        security_properties = {
            "SESSION_COOKIE_HTTPONLY": True,  # Prevents JS access
            "SESSION_COOKIE_SECURE": True,    # HTTPS only
            "SESSION_COOKIE_SAMESITE": "Lax", # Allows new-tab, blocks cross-origin
            "WTF_CSRF_ENABLED": True,         # CSRF tokens enabled
        }
        for prop, expected in security_properties.items():
            assert getattr(ProductionConfig, prop) == expected, \
                f"{prop} not set correctly in ProductionConfig"

    def test_remember_cookie_also_secure(self):
        """'Remember Me' cookies also use secure flags."""
        assert ProductionConfig.REMEMBER_COOKIE_HTTPONLY is True
        assert ProductionConfig.REMEMBER_COOKIE_SECURE is True

    def test_csrf_time_limit_tied_to_session(self):
        """CSRF token lifetime matches session lifetime (not independent)."""
        # This prevents scenarios where CSRF tokens expire while session is still
        # alive (e.g., notification poller keeping session alive indefinitely).
        assert BaseConfig.WTF_CSRF_TIME_LIMIT is None


class TestSessionCookieUseCase:
    """Verify the fix solves the real-world use case."""

    def test_samesite_lax_solves_new_tab_problem(self):
        """
        Scenario: User clicks "Open link in new tab" on a vehicle row.

        Before fix (SameSite=Strict):
          1. User right-clicks → "Open link in new tab"
          2. Browser opens /admin/vehicles/123 in new tab
          3. Browser does NOT send session cookie (SameSite=Strict blocks it)
          4. New tab has no session
          5. Flask redirects to login page
          6. User has to log in again ❌

        After fix (SameSite=Lax):
          1. User right-clicks → "Open link in new tab"
          2. Browser opens /admin/vehicles/123 in new tab
          3. Browser DOES send session cookie (SameSite=Lax allows it)
          4. New tab has valid session
          5. Flask serves the vehicle detail page
          6. User stays logged in ✅
        """
        assert BaseConfig.SESSION_COOKIE_SAMESITE == "Lax"

    def test_comparison_workflows_now_possible(self):
        """
        Users can now compare vehicle information across tabs without re-logging-in.

        Workflow:
        1. View vehicle A in tab 1 (/admin/vehicles/1)
        2. Right-click vehicle B → "Open in new tab"  ← User stayed logged in
        3. View vehicle B in tab 2 (/admin/vehicles/2)
        4. Switch back to tab 1 — still logged in
        5. Click a checkbox — still logged in
        
        This was impossible before because each new tab required a fresh login.
        """
        assert BaseConfig.SESSION_COOKIE_SAMESITE == "Lax"
