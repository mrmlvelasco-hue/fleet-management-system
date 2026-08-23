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
        "vehicle_make": s.vehicle_make,
        "vehicle_model": s.vehicle_model,
        "priority": s.priority,
        "is_active": bool(s.is_active),
    }
    if detail:
        data.update({
            "variant": s.variant,
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
        })
    return data


@bp.route("/pm-templates", methods=["GET"])
@api_auth_required("pmschedule.view")
def list_pm_templates(api_user):
    from app.modules.maintenance_config.service import PMScheduleService
    q = (request.args.get("q") or "").strip().lower()
    rows = PMScheduleService().list(include_inactive=True)
    if q:
        rows = [s for s in rows if q in " ".join(filter(None, [
            s.profile_code, s.profile_description, s.vehicle_make,
            s.vehicle_model,
            s.maintenance_type.name if s.maintenance_type else None,
            s.vehicle_type.name if s.vehicle_type else None,
        ])).lower()]
    payload, err = _page([_sched_json(s) for s in rows])
    return err if err else jsonify(payload)


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
        s = PMScheduleService().create(
            maintenance_type_id=mt_id,
            trigger_mode=trigger,
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

def _scope_json(t, *, detail=False):
    data = {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "maintenance_type_id": t.maintenance_type_id,
        "maintenance_type": (
            t.maintenance_type.name if t.maintenance_type else None),
        "pm_schedule_id": t.pm_schedule_id,
        "item_count": len(t.items) if t.items is not None else 0,
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
            }
            for i in (t.items or [])
        ]
    return data


@bp.route("/pm-scope-templates", methods=["GET"])
@api_auth_required("pmscopetemplate.view")
def list_pm_scope_templates(api_user):
    from app.modules.maintenance_config.service import PMScopeTemplateService
    q = (request.args.get("q") or "").strip().lower()
    # list_with_counts avoids loading 100k items for the index
    pairs = PMScopeTemplateService().list_with_counts(include_inactive=True)
    items = []
    for tmpl, count in pairs:
        if q and q not in " ".join(filter(None, [
            tmpl.name, tmpl.description,
            tmpl.maintenance_type.name if tmpl.maintenance_type else None,
        ])).lower():
            continue
        row = _scope_json(tmpl)
        row["item_count"] = count
        items.append(row)
    payload, err = _page(items)
    return err if err else jsonify(payload)


@bp.route("/pm-scope-templates/<int:tid>", methods=["GET"])
@api_auth_required("pmscopetemplate.view")
def get_pm_scope_template(api_user, tid):
    from app.modules.maintenance_config.service import PMScopeTemplateService
    t = PMScopeTemplateService().get_by_id(tid)
    if t is None:
        return _not_found("PM scope template")
    return jsonify(_scope_json(t, detail=True))


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
