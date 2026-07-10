"""FMS application factory."""
import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask

from app.config import CONFIG_MAP
from app.extensions import db, migrate, login_manager, csrf
from app.core.celery_app import init_celery


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("FLASK_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(CONFIG_MAP[config_name])

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    init_celery(app)
    _configure_logging(app)

    # Blueprints, error handlers, permission sync and audit hooks are
    # registered in later tasks; this factory grows as tasks land.
    return app


def _configure_logging(app: Flask) -> None:
    if app.testing:
        return
    os.makedirs("instance/logs", exist_ok=True)
    handler = RotatingFileHandler(
        "instance/logs/fms.log", maxBytes=1_000_000, backupCount=5
    )
    handler.setFormatter(
        logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    )
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
