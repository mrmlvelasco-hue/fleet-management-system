"""FMS application factory."""
import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask
from flask_login import current_user

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
    from app.modules.master_data.vehicle_brand import models as _vbm  # noqa: F401
    from app.modules.transactions.trip_ticket import models as _tt  # noqa: F401
    from app.modules.transactions.atd import models as _atd        # noqa: F401
    from app.modules.transactions.vehicle_movement import models as _vm  # noqa: F401
    from app.modules.maintenance_config import models as _pmc  # noqa: F401
    from app.modules.transactions.maintenance_order import models as _mo  # noqa: F401
    from app.modules.transactions.tire_txn import models as _tirtx  # noqa: F401
    from app.modules.transactions.battery_txn import models as _battx  # noqa: F401
    from app.modules.transactions.purchase_request import models as _pr  # noqa: F401
    from app.modules.transactions.vehicle_registration import models as _vreg  # noqa: F401

    from app.modules.auth.routes import bp as auth_bp
    from app.modules.main.routes import bp as main_bp
    from app.modules.user_management.routes import bp as user_mgmt_bp
    from app.modules.document_config.routes import bp as doc_config_bp
    from app.modules.approval_config.routes import bp as approval_config_bp
    from app.modules.system_admin.routes import bp as system_admin_bp
    from app.modules.master_data.routes import bp as master_data_bp
    from app.modules.transactions.routes import bp as transactions_bp
    from app.modules.maintenance_config.routes import bp as maintenance_config_bp
    from app.modules.registration_config.routes import bp as registration_config_bp
    from app.modules.api_search.routes import bp as api_search_bp
    from app.core.comments.routes import bp as comments_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(user_mgmt_bp)
    app.register_blueprint(doc_config_bp)
    app.register_blueprint(approval_config_bp)
    app.register_blueprint(system_admin_bp)
    app.register_blueprint(master_data_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(maintenance_config_bp)
    app.register_blueprint(registration_config_bp)
    app.register_blueprint(api_search_bp)
    app.register_blueprint(comments_bp)

    from app.modules.system_admin.services.notification_engine import (
        register_notification_hooks)
    register_notification_hooks()

    from app.modules.master_data.vehicle.assignment_hooks import (
        register_vehicle_assignment_hooks)
    register_vehicle_assignment_hooks()

    from app.cli import register_cli
    register_cli(app)

    from flask import render_template, request, jsonify
    import uuid, logging

    def _wants_json() -> bool:
        """True for fetch/$.ajax calls, so they get a JSON body the
        global SweetAlert handler in base.html can show a popup from,
        instead of an HTML error page landing inside a background
        request the person never sees."""
        return (request.headers.get("X-Requested-With") == "XMLHttpRequest"
               or request.accept_mimetypes.best == "application/json")

    @app.errorhandler(403)
    def forbidden(_e):
        if _wants_json():
            return jsonify(error="You don't have permission to do that.",
                          error_type="FORBIDDEN"), 403
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        if _wants_json():
            return jsonify(error="That wasn't found.",
                          error_type="NOT_FOUND"), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        # A short reference code the person can read off the screen (or
        # a screenshot) and give you, so you can find the matching
        # traceback in the server log without them needing to describe
        # what happened technically.
        ref = uuid.uuid4().hex[:8].upper()
        logging.getLogger(__name__).exception(
            "Unhandled 500 [ref=%s] at %s: %s", ref, request.path, e)
        if _wants_json():
            return jsonify(
                error="Something went wrong on our side.",
                error_type="SERVER_ERROR", reference=ref), 500
        return render_template("errors/500.html", reference=ref), 500

    @app.template_global()
    def lookup_user(user_id):
        """Resolves a plain user-id column (created_by/updated_by on
        BaseModel — intentionally FK-less to avoid circular deps across
        every model in the system) to the User object, for display in
        templates like the Requestor Info panel."""
        if not user_id:
            return None
        from app.modules.user_management.models import User
        return db.session.get(User, user_id)

    @app.template_global()
    def is_eligible_approver(approval_instance):
        """Whether the currently logged-in user can act on this
        instance's current pending level — used to decide whether to
        show Approve/Reject/Return buttons at all, rather than showing
        them to everyone and only failing with an error after they
        click."""
        from app.core.approval.engine import ApprovalEngine
        return ApprovalEngine().is_eligible_approver(approval_instance, current_user)

    @app.template_global()
    def approval_chain(approval_instance):
        """The full approval line for this instance — every level, its
        status, and who acted — for display on any transaction's detail
        page (the Requestor Information panel's counterpart for
        approvals)."""
        from app.core.approval.engine import ApprovalEngine
        return ApprovalEngine().get_approval_chain(approval_instance)

    @app.template_global()
    def comment_thread(reference_table, reference_id):
        """All comments posted on a document, oldest first — the
        discussion thread shown alongside the Approval Line."""
        from app.core.comments.comment_service import CommentService
        return CommentService().list_for(reference_table, reference_id)

    @app.template_global()
    def comment_attachments(comment_id):
        """Files attached to a specific comment."""
        from app.core.attachments.attachment_service import AttachmentService
        return AttachmentService().list_for("document_comments", comment_id)

    @app.template_filter("pm_tokens")
    def pm_tokens_filter(text, vehicle=None):
        """Jinja filter for print report templates: {{ text|pm_tokens(vehicle) }}
        resolves any pm2-pm9 PM Parameter Mapping tokens embedded in the
        text (e.g. imported checklist activity descriptions) using live
        data about the given vehicle."""
        from app.core.reporting.token_resolver import resolve_pm_tokens
        return resolve_pm_tokens(text, vehicle=vehicle)

    @app.template_filter("peso")
    def peso_filter(value, symbol=True):
        """Consistent currency formatting across every UI and print
        template: thousands separators and exactly 2 decimals, e.g.
        950000 -> "₱950,000.00". Returns an em dash for None/blank so
        empty money fields render consistently rather than as "None" or
        an empty cell. Accepts Decimal, float, int, or a numeric string;
        a non-numeric value is returned unchanged rather than raising, so
        one bad row never breaks a whole report."""
        if value is None or value == "":
            return "—"
        from decimal import Decimal, InvalidOperation
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return value
        formatted = "{:,.2f}".format(amount)
        return f"₱{formatted}" if symbol else formatted

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
