"""Environment-based configuration classes for the FMS application.

Select via FLASK_ENV (development / testing / production). All values are
read from environment variables so nothing is hardcoded per deployment.
NOTE (spec): SESSION_TIMEOUT / lockout threshold move to the System
Parameters module in Phase 1c; env vars are the 1a interim mechanism.
"""
import os
from datetime import timedelta
from urllib.parse import quote_plus


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
    WTF_CSRF_ENABLED = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_HTTPONLY = True


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


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
