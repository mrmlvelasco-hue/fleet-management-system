"""PM Templates (schedules) and PM Scope Templates for React.

Flask UI lives under maintenance_config. React nav already points at
/pm-templates and /pm-scope-templates as placeholders — this module is
the API those screens need.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _bad(message, code=400):
    return jsonify({"error": "bad_request", "message": message}), code


def _not_found(label="Record"):
    return jsonify({"error": "not_found", "message": f"{label} not found."}), 404


def _validation(message, field="_"):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field: message}}), 400


# Section 4 of the Jinja form. Enumerated there as a <select>; an
# arbitrary string would reach the due-calculation scheduler and match
# none of its branches, so it is rejected at the edge rather than stored
# and silently ignored later.
_NEXT_PMS_GENERATION = ("MANUAL", "AUTO_SCHEDULE", "AUTO_MO")
_NEXT_DUE_METHOD = ("ACTUAL_COMPLETION", "ORIGINAL_SCHEDULE", "ADMIN_CHOICE")


class _FieldError(ValueError):
    """Carries the field name so the response can highlight the input."""

    def __init__(self, message, field):
        super().__init__(message)
        self.field = field


def _parse_date(raw, field="effective_date"):
    """ISO date, or None for an empty value.

    "" must mean NULL rather than "not supplied": a date the user
    cleared has to actually clear, or the field becomes one-way and the
    only way to undo a mistake is a DB edit.
    """
    if raw in (None, ""):
        return None
    from datetime import date, datetime
    if isinstance(raw, date):
        return raw
    try:
        return datetime.strptime(str(raw).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        raise _FieldError(f"{field} must be an ISO date (YYYY-MM-DD).", field)


def _parse_choice(raw, allowed, field, default=None):
    if raw in (None, ""):
        return default
    value = str(raw).strip().upper()
    if value not in allowed:
        raise _FieldError(
            f"{field} must be one of: {', '.join(allowed)}.", field)
    return value


def _page(rows):
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 25))
    except (TypeError, ValueError):
        return None, _bad("page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return None, _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "items": rows[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }, None


def _sched_json(s, *, detail=False):
    data = {
        "id": s.id,
        "profile_code": s.profile_code,
        "profile_description": s.profile_description,
        "sequence_position": s.sequence_position,
        "maintenance_type_id": s.maintenance_type_id,
        "maintenance_type": (
            s.maintenance_type.name if s.maintenance_type else None),
        "trigger_mode": s.trigger_mode,
        "interval_km": s.interval_km,
        "interval_days": s.interval_days,
        "vehicle_type_id": s.vehicle_type_id,
        "vehicle_type": s.vehicle_type.name if s.vehicle_type else None,
        "vehicle_brand_id": s.vehicle_brand_id,
        "vehicle_model_id": s.vehicle_model_id,
        # The NAMES, not just the ids. The Make / Model column on the
        # list renders "{brand} {model} ({variant})" and falls back to
        # the free-text pair only when the FK pair is absent -- the
        # client cannot make that choice, or draw the cell at all, from
        # ids. Both are eager-loaded by list_paginated, so this costs
        # nothing per row.
        "vehicle_brand": s.vehicle_brand.name if s.vehicle_brand else None,
        "vehicle_model_ref": (
            s.vehicle_model_ref.name if s.vehicle_model_ref else None),
        "variant": s.variant,
        "vehicle_make": s.vehicle_make,
        "vehicle_model": s.vehicle_model,
        "priority": s.priority,
        "is_active": bool(s.is_active),
    }
    if detail:
        data.update({
            "engine_type": s.engine_type,
            "fuel_type": s.fuel_type,
            "transmission": s.transmission,
            "model_year_from": s.model_year_from,
            "model_year_to": s.model_year_to,
            "effective_date": (
                s.effective_date.isoformat() if s.effective_date else None),
            "interval_hours": s.interval_hours,
            "cumulative_km": s.cumulative_km,
            "notify_before_km": s.notify_before_km,
            "notify_before_days": s.notify_before_days,
            "escalate_if_overdue": bool(s.escalate_if_overdue),
            "work_description_template": s.work_description_template,
            "next_pms_generation": s.next_pms_generation,
            "next_due_calculation_method": s.next_due_calculation_method,
            # Detail only, deliberately. The Jinja detail screen renders
            # a collapsible activity table per linked scope template;
            # putting these on the LIST would reload every activity row
            # for every template on screen, which is the exact cost the
            # scope list was rewritten to avoid (see
            # PMScopeTemplateService.list_paginated).
            "scope_templates": [
                _scope_json(t, detail=True) | {"item_count": len(t.items or [])}
                for t in sorted(s.scope_templates or [],
                                key=lambda t: (t.name or ""))
                if t.is_active
            ],
        })
    return data


@bp.route("/pm-templates", methods=["GET"])
@api_auth_required("pmschedule.view")
def list_pm_templates(api_user):
    """One page of PM templates, paginated and filtered in SQL.

    This previously called `PMScheduleService().list(include_inactive=
    True)` -- every row in the table -- filtered in Python and sliced
    the result. On the client's 4,626-template VEMS import that built
    thousands of ORM objects, with four eager relationship loads each,
    on every single page view, to return 25 of them.

    `list_paginated` was already sitting in the same service, already
    used by the Jinja screen, already doing server-side search, the
    maintenance-type filter and the same eager loads. Using it is the
    whole fix. `test_list_never_selects_pm_schedules_without_a_limit`
    guards the regression by watching the emitted SQL rather than the
    row count -- a Python slice returns a correct-looking page while
    having already paid the full cost.
    """
    from app.modules.maintenance_config.service import PMScheduleService
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 25))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)

    mt_raw = request.args.get("maintenance_type_id")
    if mt_raw:
        try:
            int(mt_raw)
        except (TypeError, ValueError):
            return _bad("maintenance_type_id must be an integer.")

    rows, pagination = PMScheduleService().list_paginated(
        page=page, per_page=page_size,
        search=(request.args.get("q") or "").strip() or None,
        maintenance_type_id=mt_raw or None,
        include_inactive=True)
    return jsonify({
        "items": [_sched_json(s) for s in rows],
        "total": pagination.total,
        "page": pagination.page,
        "page_size": pagination.per_page,
        "pages": max(1, pagination.pages),
    })


@bp.route("/pm-templates/<int:sid>", methods=["GET"])
@api_auth_required("pmschedule.view")
def get_pm_template(api_user, sid):
    from app.modules.maintenance_config.service import PMScheduleService
    s = PMScheduleService().get_by_id(sid)
    if s is None:
        return _not_found("PM template")
    return jsonify(_sched_json(s, detail=True))


@bp.route("/pm-templates", methods=["POST"])
@api_auth_required("pmschedule.create")
def create_pm_template(api_user):
    from app.modules.maintenance_config.service import PMScheduleService
    p = request.get_json(silent=True) or {}
    try:
        mt_id = int(p["maintenance_type_id"])
    except (KeyError, TypeError, ValueError):
        return _validation("maintenance_type_id is required.", "maintenance_type_id")
    trigger = (p.get("trigger_mode") or "").strip().upper()
    if trigger not in ("KM", "CALENDAR", "HYBRID"):
        return _validation("trigger_mode must be KM, CALENDAR, or HYBRID.",
                           "trigger_mode")

    def _int(key):
        v = p.get(key)
        if v in (None, ""):
            return None
        return int(v)

    try:
        effective_date = _parse_date(p.get("effective_date"))
        next_gen = _parse_choice(
            p.get("next_pms_generation"), _NEXT_PMS_GENERATION,
            "next_pms_generation", default="AUTO_SCHEDULE")
        next_due = _parse_choice(
            p.get("next_due_calculation_method"), _NEXT_DUE_METHOD,
            "next_due_calculation_method", default="ACTUAL_COMPLETION")
    except _FieldError as e:
        return _validation(str(e), e.field)

    try:
        s = PMScheduleService().create(
            maintenance_type_id=mt_id,
            trigger_mode=trigger,
            effective_date=effective_date,
            next_pms_generation=next_gen,
            next_due_calculation_method=next_due,
            vehicle_type_id=_int("vehicle_type_id"),
            vehicle_make=p.get("vehicle_make") or None,
            vehicle_model=p.get("vehicle_model") or None,
            vehicle_brand_id=_int("vehicle_brand_id"),
            vehicle_model_id=_int("vehicle_model_id"),
            variant=p.get("variant") or None,
            engine_type=p.get("engine_type") or None,
            fuel_type=p.get("fuel_type") or None,
            transmission=p.get("transmission") or None,
            model_year_from=_int("model_year_from"),
            model_year_to=_int("model_year_to"),
            profile_code=p.get("profile_code") or None,
            profile_description=p.get("profile_description") or None,
            sequence_position=_int("sequence_position"),
            interval_km=_int("interval_km"),
            interval_days=_int("interval_days"),
            interval_hours=_int("interval_hours"),
            cumulative_km=_int("cumulative_km"),
            priority=(p.get("priority") or "MEDIUM"),
            notify_before_km=_int("notify_before_km"),
            notify_before_days=_int("notify_before_days"),
            escalate_if_overdue=bool(p.get("escalate_if_overdue", True)),
            work_description_template=p.get("work_description_template") or None,
        )
    except Exception as e:
        return _validation(str(e))
    return jsonify(_sched_json(s, detail=True)), 201


@bp.route("/pm-templates/<int:sid>", methods=["PUT", "PATCH"])
@api_auth_required("pmschedule.update")
def update_pm_template(api_user, sid):
    from app.modules.maintenance_config.service import PMScheduleService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in (
        "maintenance_type_id", "trigger_mode", "vehicle_type_id",
        "vehicle_make", "vehicle_model", "vehicle_brand_id", "vehicle_model_id",
        "variant", "engine_type", "fuel_type", "transmission",
        "model_year_from", "model_year_to", "profile_code",
        "profile_description", "sequence_position", "interval_km",
        "interval_days", "interval_hours", "cumulative_km", "priority",
        "notify_before_km", "notify_before_days", "escalate_if_overdue",
        "work_description_template",
    ):
        if k in p:
            fields[k] = p[k]
    # Parsed rather than passed through: these three are typed columns
    # (a Date and two enumerations) that were previously absent from the
    # list above entirely, so the Jinja form's Effective Date and its
    # whole "Scheduling Policy & Recalculation" section were accepted by
    # the screen and discarded by the API.
    try:
        if "effective_date" in p:
            fields["effective_date"] = _parse_date(p["effective_date"])
        if "next_pms_generation" in p:
            fields["next_pms_generation"] = _parse_choice(
                p["next_pms_generation"], _NEXT_PMS_GENERATION,
                "next_pms_generation", default="AUTO_SCHEDULE")
        if "next_due_calculation_method" in p:
            fields["next_due_calculation_method"] = _parse_choice(
                p["next_due_calculation_method"], _NEXT_DUE_METHOD,
                "next_due_calculation_method", default="ACTUAL_COMPLETION")
    except _FieldError as e:
        return _validation(str(e), e.field)
    try:
        s = PMScheduleService().update(sid, **fields)
    except Exception as e:
        return _validation(str(e))
    if s is None:
        return _not_found("PM template")
    return jsonify(_sched_json(s, detail=True))


@bp.route("/pm-templates/<int:sid>/deactivate", methods=["POST"])
@api_auth_required("pmschedule.delete")
def deactivate_pm_template(api_user, sid):
    from app.modules.maintenance_config.service import PMScheduleService
    if PMScheduleService().get_by_id(sid) is None:
        return _not_found("PM template")
    PMScheduleService().deactivate(sid)
    return jsonify({"ok": True})


# ── PM Scope Templates ──────────────────────────────────────────────────────

def _pm_schedule_label(s):
    """How a linked PM Template reads in a cell.

    Profile code first because that is what the client's own data is
    organised by (S02-00001 and friends); the maintenance type name is
    the fallback for schedules imported without one. None when there is
    no link at all, which is a real and common state -- a scope template
    with no pm_schedule_id is the generic one matched by maintenance
    type alone.
    """
    if s is None:
        return None
    if s.profile_code:
        return s.profile_code
    if s.maintenance_type is not None:
        return s.maintenance_type.name
    return f"Template #{s.id}"


def _scope_json(t, *, detail=False):
    data = {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "maintenance_type_id": t.maintenance_type_id,
        "maintenance_type": (
            t.maintenance_type.name if t.maintenance_type else None),
        "pm_schedule_id": t.pm_schedule_id,
        # The Jinja scope list has a "Linked PM Template" column. An id
        # is not renderable, and resolving it client-side would be one
        # request per row. Uses the profile code when there is one and
        # falls back to the maintenance type name, matching what the
        # Jinja cell prints.
        "pm_schedule": _pm_schedule_label(t.pm_schedule),
        # item_count is filled by the list endpoint via GROUP BY; avoid
        # len(t.items) here — that would lazy-load every activity row.
        "item_count": 0,
        "is_active": bool(t.is_active),
    }
    if detail:
        data["items"] = [
            {
                "id": i.id,
                "activity_code": i.activity_code,
                "activity_description": i.activity_description,
                "sort_order": i.sort_order,
                "standard_labor_hours": (
                    float(i.standard_labor_hours)
                    if i.standard_labor_hours is not None else None),
                "estimated_cost": (
                    float(i.estimated_cost)
                    if i.estimated_cost is not None else None),
                # A Parts column on both the scope detail table and the
                # inline table on the PM template detail. Omitted here
                # while both screens rendered it from Jinja directly.
                "required_parts": i.required_parts,
            }
            for i in (t.items or [])
        ]
    return data


@bp.route("/pm-scope-templates", methods=["GET"])
@api_auth_required("pmscopetemplate.view")
def list_pm_scope_templates(api_user):
    """Server-side page of scope templates (never load all activity rows).

    Large VEMS imports put tens of thousands of PMScopeItem rows behind a
    few thousand templates. list_paginated counts activities only for the
    current page of templates — O(page) not O(all items).
    """
    from app.modules.maintenance_config.service import PMScopeTemplateService
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 25))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    q = (request.args.get("q") or "").strip() or None
    mt = request.args.get("maintenance_type_id")
    try:
        mt_id = int(mt) if mt not in (None, "") else None
    except (TypeError, ValueError):
        return _validation("maintenance_type_id must be an integer.",
                           "maintenance_type_id")
    include_inactive = (request.args.get("include_inactive") or "1") not in (
        "0", "false", "False")

    pairs, pagination = PMScopeTemplateService().list_paginated(
        page=page, per_page=page_size, search=q,
        maintenance_type_id=mt_id, include_inactive=include_inactive)
    items = []
    for tmpl, count in pairs:
        row = _scope_json(tmpl)
        row["item_count"] = count  # never len(tmpl.items) on the list path
        items.append(row)
    return jsonify({
        "items": items,
        "total": pagination.total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, pagination.pages or 1),
    })


@bp.route("/pm-scope-templates/<int:tid>", methods=["GET"])
@api_auth_required("pmscopetemplate.view")
def get_pm_scope_template(api_user, tid):
    from app.modules.maintenance_config.service import PMScopeTemplateService
    from app.modules.maintenance_config.models import PMScopeTemplate
    from sqlalchemy.orm import selectinload
    from app.extensions import db
    t = (
        db.session.query(PMScopeTemplate)
        .options(selectinload(PMScopeTemplate.items))
        .filter_by(id=tid)
        .first()
    )
    if t is None:
        return _not_found("PM scope template")
    data = _scope_json(t, detail=True)
    data["item_count"] = len(data.get("items") or [])
    return jsonify(data)


@bp.route("/pm-scope-templates", methods=["POST"])
@api_auth_required("pmscopetemplate.create")
def create_pm_scope_template(api_user):
    from app.modules.maintenance_config.service import (
        PMScopeTemplateService, InvalidScopeError)
    p = request.get_json(silent=True) or {}
    name = (p.get("name") or "").strip()
    try:
        mt_id = int(p["maintenance_type_id"])
    except (KeyError, TypeError, ValueError):
        return _validation("maintenance_type_id is required.",
                           "maintenance_type_id")
    if not name:
        return _validation("name is required.", "name")
    raw_items = p.get("items") or []
    items = []
    for idx, it in enumerate(raw_items):
        code = (it.get("activity_code") or "").strip()
        desc = (it.get("activity_description") or it.get("activity") or "").strip()
        if not code and not desc:
            continue
        if not code:
            code = f"ACT-{idx + 1}"
        if not desc:
            desc = code
        items.append({
            "activity_code": code,
            "activity_description": desc,
            "sort_order": it.get("sort_order", idx + 1),
            "standard_labor_hours": it.get("standard_labor_hours"),
            "estimated_cost": it.get("estimated_cost"),
        })
    try:
        t = PMScopeTemplateService().create(
            maintenance_type_id=mt_id,
            name=name,
            description=p.get("description") or None,
            pm_schedule_id=p.get("pm_schedule_id") or None,
            items=items,
        )
    except InvalidScopeError as e:
        return _validation(str(e), "items")
    return jsonify(_scope_json(t, detail=True)), 201


@bp.route("/pm-scope-templates/<int:tid>", methods=["PUT", "PATCH"])
@api_auth_required("pmscopetemplate.update")
def update_pm_scope_template(api_user, tid):
    from app.modules.maintenance_config.service import (
        PMScopeTemplateService, InvalidScopeError)
    p = request.get_json(silent=True) or {}
    kwargs = {}
    if "name" in p:
        kwargs["name"] = (p.get("name") or "").strip()
    if "description" in p:
        kwargs["description"] = p.get("description")
    if "pm_schedule_id" in p:
        kwargs["pm_schedule_id"] = p.get("pm_schedule_id")
    if "items" in p:
        items = []
        for idx, it in enumerate(p.get("items") or []):
            code = (it.get("activity_code") or "").strip()
            desc = (it.get("activity_description") or it.get("activity") or "").strip()
            if not code and not desc:
                continue
            if not code:
                code = f"ACT-{idx + 1}"
            if not desc:
                desc = code
            items.append({
                "activity_code": code,
                "activity_description": desc,
                "sort_order": it.get("sort_order", idx + 1),
                "standard_labor_hours": it.get("standard_labor_hours"),
                "estimated_cost": it.get("estimated_cost"),
            })
        kwargs["items"] = items
    try:
        t = PMScopeTemplateService().update(tid, **kwargs)
    except InvalidScopeError as e:
        return _validation(str(e), "items")
    if t is None:
        return _not_found("PM scope template")
    return jsonify(_scope_json(t, detail=True))


@bp.route("/pm-scope-templates/<int:tid>/deactivate", methods=["POST"])
@api_auth_required("pmscopetemplate.delete")
def deactivate_pm_scope_template(api_user, tid):
    from app.modules.maintenance_config.service import PMScopeTemplateService
    if PMScopeTemplateService().get_by_id(tid) is None:
        return _not_found("PM scope template")
    PMScopeTemplateService().deactivate(tid)
    return jsonify({"ok": True})


# ── PMS Profiles (grouped view of PM schedules by profile_code) ─────────────

@bp.route("/pms-profiles", methods=["GET"])
@api_auth_required("pmprofile.view")
def list_pms_profiles(api_user):
    from app.modules.maintenance_config.service import PMSProfileService
    q = (request.args.get("q") or "").strip().lower()
    profiles = PMSProfileService().list_profiles()
    items = []
    for p in profiles:
        brand = p.get("vehicle_brand")
        model = p.get("vehicle_model_ref")
        row = {
            "profile_code": p["profile_code"],
            "description": p.get("description"),
            "package_count": p.get("package_count", 0),
            "vehicle_brand": brand.name if brand else None,
            "vehicle_model": model.name if model else None,
        }
        if q and q not in " ".join(filter(None, [
            row["profile_code"], row["description"],
            row["vehicle_brand"], row["vehicle_model"],
        ])).lower():
            continue
        items.append(row)
    items.sort(key=lambda r: (r["profile_code"] or "").lower())
    payload, err = _page(items)
    return err if err else jsonify(payload)


@bp.route("/pms-profiles/<path:profile_code>", methods=["GET"])
@api_auth_required("pmprofile.view")
def get_pms_profile(api_user, profile_code):
    from app.modules.maintenance_config.service import PMSProfileService
    packages = PMSProfileService().get_profile(profile_code)
    if not packages:
        return _not_found("PMS Profile")
    first = packages[0]
    return jsonify({
        "profile_code": profile_code,
        "description": first.profile_description,
        "vehicle_brand": (
            first.vehicle_brand.name if first.vehicle_brand else None),
        "vehicle_model": (
            first.vehicle_model_ref.name if first.vehicle_model_ref else None),
        "packages": [
            {
                "id": pkg.id,
                "sequence_position": pkg.sequence_position,
                "maintenance_type": (
                    pkg.maintenance_type.name if pkg.maintenance_type else None),
                "trigger_mode": pkg.trigger_mode,
                "interval_km": pkg.interval_km,
                "interval_days": pkg.interval_days,
                "priority": pkg.priority,
                "is_active": bool(pkg.is_active),
            }
            for pkg in packages
        ],
    })


# ── Export & Import ─────────────────────────────────────────────────────────

@bp.route("/pm-templates/export", methods=["GET"])
@api_auth_required("pmschedule.view")
def export_pm_templates(api_user):
    """The same XLSX the Jinja Export button produces, over bearer auth.

    The Jinja button points at `master_data_export`, which is
    `@login_required` and reads `current_user` -- a session route. React
    holds a bearer token and no session cookie, so it could not reach
    it, and copying the generator would have meant two exports that
    drift. This reuses `generate_master_data_xlsx` unchanged and only
    swaps the authentication in front of it.

    Guarded on `pmschedule.view`, matching `_EXPORT_PERMISSIONS` on the
    Jinja side: an export is a full dump of the module, so it must
    require exactly what viewing the screen requires. Anything looser
    hands someone every row they cannot see on screen.
    """
    from io import BytesIO

    from flask import send_file

    from app.core.reporting.master_data_exports import (
        generate_master_data_xlsx)

    data, filename = generate_master_data_xlsx("pm-schedules")
    return send_file(
        BytesIO(data), as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")


@bp.route("/pm-templates/import", methods=["POST"])
@api_auth_required("pmschedule.create")
def import_pm_templates(api_user):
    """CSV import, reusing PMScheduleImportService unchanged.

    Requires `pmschedule.create`, not `.view` -- this writes rows.

    Row-level errors come back with a 200 and the created/skipped
    counts, exactly as the Jinja screen renders them. Failing the whole
    request on one bad row would tell the user their file was rejected
    without telling them which line to fix, and would throw away the
    rows that were fine.
    """
    import io

    file = request.files.get("csv_file")
    if file is None or not file.filename:
        return _validation("Please choose a CSV file.", "csv_file")

    from app.modules.maintenance_config.import_service import (
        PMScheduleImportService)
    try:
        content = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return _validation(
            "That file is not UTF-8 text. Export it as CSV UTF-8 and "
            "try again.", "csv_file")

    result = PMScheduleImportService().import_csv(io.StringIO(content))
    return jsonify({
        "created": result.get("created", 0),
        "skipped": result.get("skipped", 0),
        "errors": result.get("errors", []),
    })
