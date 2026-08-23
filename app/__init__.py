"""FMS application factory."""
import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask
from flask_login import current_user

from app.config import CONFIG_MAP
from urllib.parse import urlparse

from flask_wtf.csrf import CSRFError

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
    from app.core import data_quality_service as _dq  # noqa: F401
    from app.modules.transactions.fuel import models as _fuel  # noqa: F401
    from app.modules.history_migration import models as _histmig  # noqa: F401
    from app.modules.transactions.maintenance_order.auto_pr import (
        register_auto_pr)
    register_auto_pr(app)

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
    from app.modules.api.routes import bp as api_v1_bp
    # Imported for its side effect: dashboard.py attaches its routes to
    # the api_v1 blueprint, so it must be imported BEFORE the blueprint
    # is registered -- Flask freezes a blueprint's route list at
    # registration time and silently ignores anything added afterwards.
    from app.modules.api import dashboard as _api_dashboard  # noqa: F401
    from app.modules.api import attachments as _api_attachments  # noqa: F401
    from app.modules.api import vehicles as _api_vehicles  # noqa: F401
    from app.modules.api import reference as _api_reference  # noqa: F401
    from app.modules.api import drivers as _api_drivers  # noqa: F401
    from app.modules.api import tires as _api_tires  # noqa: F401
    app.register_blueprint(api_v1_bp)
    # CSRF protects COOKIE-authenticated form posts: the browser attaches
    # the session automatically, so a third-party page could otherwise
    # trigger an authenticated request. The API authenticates from an
    # explicit Authorization header that no cross-site page can cause a
    # client to send, so CSRF adds nothing here and would simply block
    # every non-browser client (GPS units, the mobile app).
    csrf.exempt(api_v1_bp)

    # Cross-origin support for the React frontend's dev server. Scoped to
    # /api/ and driven by CORS_ORIGINS config -- see app/core/cors.py.
    from app.core.cors import init_cors
    init_cors(app)

    from app.modules.system_admin.services.notification_engine import (
        register_notification_hooks)
    register_notification_hooks()

    from app.modules.master_data.vehicle.assignment_hooks import (
        register_vehicle_assignment_hooks)
    register_vehicle_assignment_hooks()

    from app.cli import register_cli
    @app.context_processor
    def inject_attachment_doc_types():
        """Document types for the attachment upload picker.

        A context processor rather than a per-route variable because the
        attachment panel is included from well over a dozen templates
        (vehicle, driver, branch, vendor, every transaction detail...).
        Threading the same list through every one of those routes would
        mean the picker silently rendering empty on whichever one got
        missed -- and an empty dropdown looks like a broken feature, not
        an unconfigured one.

        Never raises: this runs on every render, including error pages
        and the login screen, where the lookup table may not even be
        reachable yet.
        """
        try:
            from app.core.attachments.attachment_service import (
                AttachmentService)
            return {"attachment_doc_types": AttachmentService().document_types()}
        except Exception:
            app.logger.exception("Attachment document types unavailable")
            return {"attachment_doc_types": []}

    register_cli(app)

    @app.context_processor
    def inject_sidebar_skin():
        """Resolve the sidebar appearance once per request and hand it
        to every template.

        Done server-side on purpose. If the skin were applied by JS
        after the page painted, every single page load would briefly
        show the default sidebar and then repaint -- a visible flicker
        on each navigation. Rendering the attribute into the markup
        means the correct sidebar is there in the very first paint.

        Never raises: the login page and the error pages render this
        same shell with no authenticated user.
        """
        from flask_login import current_user as _cu
        from app.core.appearance.skin_service import (
            SidebarSkinService, DEFAULT_SKIN)
        svc = SidebarSkinService()
        try:
            user = _cu if getattr(_cu, "is_authenticated", False) else None
            return {"sidebar_skin": svc.resolve(user),
                    "sidebar_skins": svc.list_skins()}
        except Exception:
            app.logger.exception("Sidebar skin resolution failed")
            return {"sidebar_skin": DEFAULT_SKIN,
                    "sidebar_skins": svc.list_skins()}

    from flask import (render_template, request, jsonify, redirect,
                       url_for, flash)
    import uuid, logging

    def _wants_json() -> bool:
        """True for fetch/$.ajax calls, so they get a JSON body the
        global SweetAlert handler in base.html can show a popup from,
        instead of an HTML error page landing inside a background
        request the person never sees."""
        return (request.headers.get("X-Requested-With") == "XMLHttpRequest"
               or request.accept_mimetypes.best == "application/json")

    @app.errorhandler(CSRFError)
    def csrf_expired(_e):
        """A CSRF failure in this app almost always means the SESSION
        expired, not an attack.

        Sessions last 30 minutes. Someone who opens a Maintenance Order,
        goes to a meeting, comes back and clicks Cancel gets a token that
        no longer matches -- and Flask-WTF's default response is a bare
        white "Bad Request / The CSRF tokens do not match" page with no
        navigation and no explanation. That looks like the system is
        broken, and it strands them with no way back.

        The honest reading is "your session ended, sign in again", so
        that is what we say. The page they were on is passed as `next`
        so they land back where they were after logging in -- but only
        for a GET-able path: the original POST (a cancel, an approve)
        deliberately is NOT replayed, since re-firing a state-changing
        action automatically after a re-login is exactly how someone
        cancels an order they never meant to.
        """
        if _wants_json():
            # Background requests get a machine-readable answer plus a
            # redirect target, so the front-end can send the person to
            # the login page rather than silently swallowing the error.
            return jsonify(
                error="Your session has expired. Please sign in again.",
                error_type="SESSION_EXPIRED",
                login_url=url_for("auth.login")), 401

        flash("Your session expired, so that action was not completed. "
              "Please sign in again.", "warning")
        target = request.referrer
        # Only offer to return somewhere on THIS site, and never to the
        # login page itself (which would loop).
        safe_next = None
        if target:
            parsed = urlparse(target)
            if (not parsed.netloc
                    or parsed.netloc == urlparse(request.host_url).netloc):
                if not parsed.path.rstrip("/").endswith("/login"):
                    safe_next = parsed.path
        return redirect(url_for("auth.login", next=safe_next)
                       if safe_next else url_for("auth.login"))

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
        # MUST happen first, before anything else touches the database.
        # A DB-related exception (a constraint violation, a deadlock)
        # leaves the SQLAlchemy session in a "pending rollback" state --
        # every further query on it raises PendingRollbackError instead
        # of running, INCLUDING the ones this very error page needs
        # (current_user.is_authenticated in base.html triggers a lazy
        # load). Without this rollback, that second failure replaces our
        # own styled error page -- with its reference code the person
        # can actually report back -- with Flask/Werkzeug's raw fallback
        # page, which is what was seen in the reported screenshot: no
        # reference code, no branding, just "Internal Server Error" in
        # the browser's default serif font. The original exception is
        # still the one logged below; this only clears the session so
        # OUR page can render at all.
        try:
            db.session.rollback()
        except Exception:
            pass

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
