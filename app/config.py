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


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = _build_database_uri()
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


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
