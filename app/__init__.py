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

    # Import all model modules so SQLAlchemy metadata is complete.
    from app.core.models import audit_log        # noqa: F401
    from app.core.models import attachment        # noqa: F401
    from app.core.approval import models as _apm  # noqa: F401
    from app.modules.user_management import models as _um  # noqa: F401
    from app.modules.document_config import models as _dc  # noqa: F401
    from app.modules.approval_config import models as _ac  # noqa: F401
    from app.modules.system_admin import models as _sa     # noqa: F401
    from app.modules.master_data.org import models as _org       # noqa: F401
    from app.modules.master_data.reference import models as _ref  # noqa: F401
    from app.modules.master_data.vendor import models as _vnd    # noqa: F401
    from app.modules.master_data.vehicle import models as _veh   # noqa: F401
    from app.modules.master_data.driver import models as _drv    # noqa: F401
    from app.modules.master_data.tire import models as _tir      # noqa: F401
    from app.modules.master_data.battery import models as _bat   # noqa: F401
    from app.modules.transactions.trip_ticket import models as _tt  # noqa: F401
    from app.modules.transactions.atd import models as _atd        # noqa: F401
    from app.modules.transactions.vehicle_movement import models as _vm  # noqa: F401

    from app.modules.auth.routes import bp as auth_bp
    from app.modules.main.routes import bp as main_bp
    from app.modules.user_management.routes import bp as user_mgmt_bp
    from app.modules.document_config.routes import bp as doc_config_bp
    from app.modules.approval_config.routes import bp as approval_config_bp
    from app.modules.system_admin.routes import bp as system_admin_bp
    from app.modules.master_data.routes import bp as master_data_bp
    from app.modules.transactions.routes import bp as transactions_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(user_mgmt_bp)
    app.register_blueprint(doc_config_bp)
    app.register_blueprint(approval_config_bp)
    app.register_blueprint(system_admin_bp)
    app.register_blueprint(master_data_bp)
    app.register_blueprint(transactions_bp)

    from app.modules.system_admin.services.notification_engine import (
        register_notification_hooks)
    register_notification_hooks()

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
    log_dir = os.path.join(app.instance_path, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, "fms.log"), maxBytes=1_000_000, backupCount=5
    )
    handler.setFormatter(
        logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    )
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
