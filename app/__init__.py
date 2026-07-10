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

    from app.core.audit.audit_service import register_audit_listeners
    register_audit_listeners()

    from app.modules.auth.routes import bp as auth_bp
    from app.modules.main.routes import bp as main_bp
    from app.modules.user_management.routes import bp as user_mgmt_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(user_mgmt_bp)

    from app.cli import register_cli
    register_cli(app)

    from flask import render_template

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("errors/500.html"), 500

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
