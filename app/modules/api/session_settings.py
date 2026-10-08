"""Session timeout and warning-before-logout, actually reaching the
React app.

SESSION_TIMEOUT_MINUTES has existed as a System Parameter since the
beginning -- seeded, shown in System Parameters, described as "Session
timeout in minutes" -- and the ONLY thing that read it was
app/config.py's PERMANENT_SESSION_LIFETIME, which governs Flask's own
cookie-based `session` (flask-login, the legacy server-rendered pages).
The React SPA authenticates entirely through JWT access/refresh
tokens and never touches that session at all, so editing this
parameter in System Parameters had zero effect on how long someone
could walk away from the React app and remain logged in. Same shape of
bug LIST_PAGES had (see pagination.py), same fix: give the SPA its own
live read of the value, cached per request via flask.g.

SESSION_WARNING_MINUTES is new -- how long before the timeout fires
the client should show a "you're about to be logged out" countdown,
so a logout is never a surprise mid-task.

Both are floored/capped defensively for the same reason
default_page_size() is: a misconfigured value must degrade to
something safe, never lock everyone out or break login outright.
"""
from flask import g

from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)

#: Used when the parameter is missing or unreadable. Matches the
#: seeded value so a database without the row behaves like one with it.
FALLBACK_SESSION_TIMEOUT_MINUTES = 30
FALLBACK_SESSION_WARNING_MINUTES = 2

#: Below this, a typo (entering "1" meaning "10") would log everyone
#: out within a minute of any idle moment -- not a security posture
#: anyone actually wants, just a fat-fingered parameter.
_MIN_TIMEOUT_MINUTES = 5


def session_timeout_minutes() -> int:
    cached = getattr(g, "_fms_session_timeout", None)
    if cached is not None:
        return cached

    try:
        value = SystemParameterService().get(
            "SESSION_TIMEOUT_MINUTES",
            default=FALLBACK_SESSION_TIMEOUT_MINUTES)
        minutes = int(value)
        if minutes < _MIN_TIMEOUT_MINUTES:
            minutes = FALLBACK_SESSION_TIMEOUT_MINUTES
    except Exception:
        # A misconfigured parameter must never break login. Everyone
        # falls back to the safe default and an administrator can fix
        # the value in System Parameters.
        minutes = FALLBACK_SESSION_TIMEOUT_MINUTES

    g._fms_session_timeout = minutes
    return minutes


def session_warning_minutes() -> int:
    cached = getattr(g, "_fms_session_warning", None)
    if cached is not None:
        return cached

    try:
        value = SystemParameterService().get(
            "SESSION_WARNING_MINUTES",
            default=FALLBACK_SESSION_WARNING_MINUTES)
        minutes = int(value)
        if minutes < 1:
            minutes = FALLBACK_SESSION_WARNING_MINUTES
    except Exception:
        minutes = FALLBACK_SESSION_WARNING_MINUTES

    # Never allowed to reach or exceed the timeout itself -- a warning
    # window as long as the session would show the countdown modal
    # before the person has finished logging in.
    timeout = session_timeout_minutes()
    if minutes >= timeout:
        minutes = max(1, timeout // 2)

    g._fms_session_warning = minutes
    return minutes


# ── SESSION_REMEMBER_POLICY ─────────────────────────────────────────────
#: A -- the refresh token lives exactly SESSION_TIMEOUT_MINUTES and is
#: renewed on every refresh: a server-enforced idle window, so walking
#: away longer than the timeout means signing in again even if the
#: browser was closed (the idle timer in the page dies with the tab).
ALWAYS_TIMEOUT = "ALWAYS_TIMEOUT"
#: B -- previous behaviour: "Remember me" keeps the 14-day refresh cookie.
REMEMBER_EXTENDS = "REMEMBER_EXTENDS"
_POLICIES = (ALWAYS_TIMEOUT, REMEMBER_EXTENDS)


def session_remember_policy() -> str:
    """ALWAYS_TIMEOUT (default) or REMEMBER_EXTENDS. Missing, blank or
    unknown values fall back to ALWAYS_TIMEOUT: a typo must fail safe,
    never quietly open up 14-day sessions."""
    cached = getattr(g, "_fms_remember_policy", None)
    if cached is not None:
        return cached
    try:
        value = str(SystemParameterService().get(
            "SESSION_REMEMBER_POLICY", default=ALWAYS_TIMEOUT) or "")
        policy = value.strip().upper()
        if policy not in _POLICIES:
            policy = ALWAYS_TIMEOUT
    except Exception:
        policy = ALWAYS_TIMEOUT
    g._fms_remember_policy = policy
    return policy


def refresh_token_lifetime():
    """How long a refresh token is valid under the current policy:
    SESSION_TIMEOUT_MINUTES under ALWAYS_TIMEOUT, None (= the default
    14 days) under REMEMBER_EXTENDS."""
    from datetime import timedelta
    if session_remember_policy() == ALWAYS_TIMEOUT:
        return timedelta(minutes=session_timeout_minutes())
    return None
