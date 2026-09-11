"""Environment-based configuration classes for the FMS application.

Select via FLASK_ENV (development / testing / production). All values are
read from environment variables so nothing is hardcoded per deployment.
NOTE (spec): SESSION_TIMEOUT / lockout threshold move to the System
Parameters module in Phase 1c; env vars are the 1a interim mechanism.
"""
import os
from datetime import timedelta
from urllib.parse import quote_plus

# Nothing else in this codebase calls load_dotenv() -- wsgi.py just does
# `create_app()`. Whether .env actually reaches os.environ therefore
# depended entirely on the LAUNCHER: `flask run` auto-loads it via
# Flask's CLI, but a PyCharm run configuration that executes wsgi.py
# directly does not, unless the IDE itself is configured with an EnvFile
# plugin pointed at the file -- and if it instead has environment
# variables typed directly into the run configuration, .env is not being
# read at all, and no amount of editing that file or restarting the
# process will ever change anything. That is indistinguishable, from
# outside, from a value simply "not taking" no matter how many times it
# is corrected.
#
# find_dotenv() walks up from the current working directory, so this
# works whether Flask is launched from the project root or from a
# subdirectory. python-dotenv never overrides a variable already present
# in os.environ, so an env var set by the real shell or CI still wins --
# this only fills in what nothing else already provided.
def _dotenv_candidates() -> list:
    """Where to look for .env, in order of precedence.

    Two locations, because one is not enough:

      1. Upward from the CURRENT WORKING DIRECTORY. This is what
         find_dotenv(usecwd=True) does on its own, and it is right for
         `flask run` from the project root. It also lets a developer
         deliberately point the app at a different file by launching
         from elsewhere, so it is searched FIRST.

      2. Beside the application package, derived from THIS FILE's own
         path. This is the one that makes discovery independent of the
         launcher.

    (2) exists because (1) searches upward only. A PyCharm run
    configuration whose working directory is the repository root, with
    the application in a subdirectory, puts .env BELOW the search --
    never above it. Nothing is found, nothing is logged, and every
    variable quietly falls back to its default. What that looked like in
    practice was a setting that would not take effect no matter how many
    times the file was corrected.
    """
    candidates = []
    try:
        from dotenv import find_dotenv
        found = find_dotenv(usecwd=True)
        if found:
            candidates.append(os.path.abspath(found))
    except ImportError:
        pass
    # config.py lives at app/config.py, so the project root -- where
    # .env sits next to wsgi.py and requirements.txt -- is two levels up.
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(package_root, ".env"))
    # Preserve order while removing the duplicate that appears whenever
    # the app is launched from its own root and both resolve to the same
    # file.
    seen, unique = set(), []
    for path in candidates:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


try:
    from dotenv import load_dotenv
    for _candidate in _dotenv_candidates():
        # override=False throughout: a variable already exported by the
        # real shell, by CI, or typed into an IDE run configuration must
        # still win. This only fills in what nothing else provided --
        # which also means the FIRST file to define a key wins, matching
        # the documented precedence above.
        load_dotenv(_candidate, override=False)
except ImportError:
    pass  # python-dotenv is in requirements.txt; this is a defensive
          # fallback only, not the expected path.


#: Values that switch a boolean env var OFF. Anything else -- including
#: a typo, an empty string, or an unset variable -- leaves it ON.
#:
#: Deliberately an allow-list rather than the usual `value.lower() not in
#: ("false", "0")` shortcut. For REFRESH_COOKIE_SECURE the cost of
#: guessing wrong is asymmetric: reading "flase" as false would ship an
#: insecure cookie to production, while reading it as true only fails a
#: LAN test in a way the operator sees immediately.
_FALSEY = frozenset({"0", "false", "no", "off"})


def _refresh_cookie_secure(env=None) -> bool:
    """Whether the refresh cookie carries the Secure attribute.

    Its OWN switch, not a consequence of DEBUG.

    This used to be `not DEBUG`, which tied a security attribute of the
    authentication cookie to the flag that also enables the interactive
    debugger -- two things with no reason to move together. It also made
    the React app's "open link in new tab" flow depend on how Flask was
    launched: a PyCharm run configuration whose working directory sat
    above the project never loaded .env, FLASK_ENV came out unset, and
    the cookie arrived at a plain-http LAN address carrying Secure.
    Chrome drops such a cookie silently. Login still looked fine,
    because the access token travels in the JSON body; the refresh
    cookie was simply never stored, and the failure surfaced one step
    later as a 401 from /auth/refresh in the new tab.

    Defaults to True so that an operator who sets nothing gets the safe
    value, and an on-prem HTTPS deployment needs no configuration at
    all. Set REFRESH_COOKIE_SECURE=0 only for http testing on a trusted
    LAN.
    """
    env = os.environ if env is None else env
    return str(env.get("REFRESH_COOKIE_SECURE", "")).strip().lower() \
        not in _FALSEY


def _build_database_uri() -> str:
    """Build the SQLAlchemy URI.

    Priority: explicit DATABASE_URL > DB_* (MySQL) vars > SQLite fallback.
    Using PyMySQL as the driver (pure Python, no system libmysqlclient
    needed) keeps local setup simple while remaining MySQL-compatible for
    production and portable to Microsoft SQL Server later.
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    db_name = os.environ.get("DB_NAME")
    if db_name:
        user = quote_plus(os.environ.get("DB_USER", "root"))
        password = quote_plus(os.environ.get("DB_PASSWORD", ""))
        host = os.environ.get("DB_HOST", "127.0.0.1")
        port = os.environ.get("DB_PORT", "3306")
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}?charset=utf8mb4"

    return "sqlite:///fms_dev.db"


def _build_engine_options(uri: str) -> dict:
    """pool_size/max_overflow/pool_recycle are QueuePool (MySQL) options
    -- SQLite's StaticPool/NullPool don't accept them at all and raise
    TypeError on create_engine() if passed, so these are only included
    for an actual MySQL URI. pool_pre_ping is safe and useful either
    way, so it always applies."""
    options = {"pool_pre_ping": True}
    if uri.startswith("mysql"):
        options.update({
            "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE_SECONDS", "280")),
            "pool_size": int(os.environ.get("DB_POOL_SIZE", "10")),
            "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "20")),
        })
    return options


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Hard ceiling on a request body, enforced by Werkzeug BEFORE the
    # request is read into memory.
    #
    # AttachmentService already enforces ATTACHMENT_MAX_SIZE_MB (default
    # 10MB) with a readable message, but that check only runs AFTER
    # Flask has buffered the entire upload -- so a 200MB file was fully
    # received and held in memory before anything rejected it. This
    # stops it at the door.
    #
    # Set well above the per-file attachment cap so the service's own
    # message, which names the real limit, is what people normally see;
    # this is the backstop, not the policy.
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024

    SQLALCHEMY_DATABASE_URI = _build_database_uri()
    # Without this, SQLAlchemy uses its bare defaults (pool_size=5,
    # max_overflow=10, no recycle, no pre-ping) -- fine for one person
    # testing locally, but a real gap for multiple concurrent users
    # against MySQL specifically: MySQL closes idle connections after
    # `wait_timeout` (often much less than MySQL's own 8-hour default on
    # shared/managed hosts), and a connection sitting idle in the pool
    # past that point fails the NEXT request that tries to reuse it with
    # "MySQL server has gone away" -- intermittently, under real traffic
    # patterns, not during quick manual testing where connections are
    # reused immediately. pool_pre_ping adds a lightweight check before
    # handing out a pooled connection and transparently reconnects if it
    # died for any other reason (network blip, DB restart) too.
    # pool_size/max_overflow are configurable per deployment since the
    # right number depends on how many worker processes x threads the
    # WSGI server runs and MySQL's own max_connections limit.
    SQLALCHEMY_ENGINE_OPTIONS = _build_engine_options(SQLALCHEMY_DATABASE_URI)
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30"))
    )
    MAX_FAILED_LOGIN_ATTEMPTS = int(os.environ.get("MAX_FAILED_LOGIN_ATTEMPTS", "5"))
    CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    # Origins allowed to call /api/ from a browser. Comma-separated in
    # the environment; empty by default, so cross-origin access is
    # off unless deliberately switched on for a known frontend host.
    CORS_ORIGINS = [o.strip() for o in
                    os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    WTF_CSRF_ENABLED = True
    # Tie the CSRF token's lifetime to the SESSION rather than letting it
    # run its own independent clock (Flask-WTF defaults to 1 hour).
    #
    # With two separate clocks the two can disagree: the notification
    # poller keeps a session alive indefinitely while a page sits open,
    # so after an hour that page's token could expire even though the
    # person is still perfectly well logged in. They would then get a
    # CSRF failure and be told their session had expired -- which would
    # simply be untrue, and bouncing them to a login page they don't need
    # is worse than the original error.
    #
    # With None the token stays valid exactly as long as the session it
    # belongs to, so a CSRF failure means what the error message says it
    # means. The token is still bound to the session and still verified;
    # this changes WHEN it lapses, not WHETHER it is checked.
    WTF_CSRF_TIME_LIMIT = None
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_HTTPONLY = True
    # SameSite="Lax" allows session cookies to be sent when users click
    # "Open link in new tab" or "Open in new window" (cross-tab navigation).
    # This is the industry standard for web apps and maintains security:
    # it blocks cross-origin cookie theft and CSRF attacks while allowing
    # the normal user workflow of viewing multiple vehicles in parallel tabs.
    #
    # Before this fix, SameSite defaulted to "Strict", which blocked
    # cookies on cross-tab navigation, forcing users to log in again
    # every time they opened a link in a new tab — making it impossible
    # to compare vehicle info between tabs without a back button redirect.
    SESSION_COOKIE_SAMESITE = "Lax"
    # The API refresh cookie's Secure attribute. See
    # _refresh_cookie_secure() for why this is not derived from DEBUG.
    # Secure unless deliberately switched off for http LAN testing.
    REFRESH_COOKIE_SECURE = _refresh_cookie_secure()


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # in-memory
    WTF_CSRF_ENABLED = False
    # BaseConfig computed SQLALCHEMY_ENGINE_OPTIONS from ITS OWN URI
    # (MySQL or the dev SQLite fallback) before this override took
    # effect, so it must be recomputed here against the actual in-memory
    # SQLite URI this class uses -- otherwise it would inherit
    # MySQL-only pool options that StaticPool rejects outright.
    SQLALCHEMY_ENGINE_OPTIONS = _build_engine_options(SQLALCHEMY_DATABASE_URI)


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    # Explicitly set (inherited from BaseConfig, but making it visible).
    SESSION_COOKIE_SAMESITE = "Lax"
    # Pinned, NOT read from the environment.
    #
    # The escape hatch exists for http testing on a trusted LAN; there
    # is no such thing as a legitimate insecure refresh cookie in
    # production. Hard-coding it here means a REFRESH_COOKIE_SECURE=0
    # left behind in a shell profile or a copied .env cannot follow the
    # app into production and quietly downgrade it.
    REFRESH_COOKIE_SECURE = True


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
