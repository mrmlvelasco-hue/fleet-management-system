"""Generic /api/search/<module> blueprint — powers AJAX-backed Select2
smart selectors across the system. Each module registers a SearchableService
subclass. These endpoints are intentionally gated by @login_required only,
not a per-module `.view` permission: they're shared reference-data lookups
used to populate dropdowns inside many *other* forms and permissions (e.g.
Maintenance Order needs the Vehicle and Vendor lookups without requiring
`vehicle.view`/`vendor.view`). Gating them behind the "owning" module's
management permission broke every other module that references that data —
Select2 showed a generic "The results could not be loaded" on any 403.
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

from app.core.search.searchable_service import SearchableService
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.vendor.models import Vendor
from app.modules.master_data.org.models import Branch
from app.modules.user_management.models import User

bp = Blueprint("api_search", __name__, url_prefix="/api/search")


class VehicleSearchService(SearchableService):
    model = Vehicle
    search_fields = ["plate_number", "conduction_number", "brand", "model"]
    sortable_fields = ["plate_number", "brand", "model", "year",
                       "current_odometer", "status"]

    def label(self, obj):
        ident = obj.plate_number or obj.conduction_number
        return f"{ident} — {obj.brand} {obj.model} ({obj.year})"

    def row(self, obj):
        return {
            "id": obj.id,
            "plate": obj.plate_number or obj.conduction_number or "—",
            "brand": obj.brand, "model": obj.model, "year": obj.year,
            "branch": obj.branch.name if obj.branch else "—",
            "status": obj.status,
            "text": self.label(obj),
        }


class DriverSearchService(SearchableService):
    model = Driver
    search_fields = ["employee_number", "first_name", "last_name"]

    def label(self, obj):
        return (f"{obj.employee_number} — {obj.last_name}, {obj.first_name} "
               f"({obj.license_type})")


class UserSearchService(SearchableService):
    model = User
    search_fields = ["username", "first_name", "last_name"]

    def label(self, obj):
        return f"{obj.username} — {obj.full_name}"


class VendorSearchService(SearchableService):
    model = Vendor
    search_fields = ["code", "name"]

    def label(self, obj):
        return f"{obj.code} — {obj.name}"


class BranchSearchService(SearchableService):
    model = Branch
    search_fields = ["code", "name"]

    def label(self, obj):
        return f"{obj.code} — {obj.name}"


def _search_params():
    return {
        "q": request.args.get("q", ""),
        "page": int(request.args.get("page", 1)),
        "per_page": int(request.args.get("per_page", 20)),
    }


@bp.route("/vehicles")
@login_required
def search_vehicles():
    return jsonify(VehicleSearchService().to_select2_response(
        user=current_user, **_search_params()))


@bp.route("/vehicles/table")
@login_required
def search_vehicles_table():
    """Table-mode endpoint for the Search Modal: sortable, filterable,
    paginated — used when a dataset is large enough that a dropdown alone
    isn't a good fit (System Administration UX rule: modal for >100 records)."""
    params = _search_params()
    params["sort_by"] = request.args.get("sort_by")
    params["sort_dir"] = request.args.get("sort_dir", "asc")
    branch_id = request.args.get("branch_id")
    status = request.args.get("status")
    if branch_id:
        params["branch_id"] = int(branch_id)
    if status:
        params["status"] = status
    return jsonify(VehicleSearchService().to_table_response(
        user=current_user, **params))


@bp.route("/drivers")
@login_required
def search_drivers():
    return jsonify(DriverSearchService().to_select2_response(
        user=current_user, **_search_params()))


@bp.route("/users")
@login_required
def search_users():
    return jsonify(UserSearchService().to_select2_response(**_search_params()))


@bp.route("/vendors")
@login_required
def search_vendors():
    return jsonify(VendorSearchService().to_select2_response(**_search_params()))


@bp.route("/branches")
@login_required
def search_branches():
    return jsonify(BranchSearchService().to_select2_response(**_search_params()))


@bp.route("/vehicle-models")
@login_required
def search_vehicle_models():
    """Cascading Model list for a given Brand — powers the Vehicle master
    form's Brand→Model selector (Model options depend on the selected
    Brand, so this isn't a generic /table search, just a filtered list)."""
    from app.modules.master_data.vehicle_brand.service import VehicleModelService
    brand_id = request.args.get("brand_id")
    if not brand_id:
        return jsonify({"results": []})
    models = VehicleModelService().list(brand_id=int(brand_id))
    return jsonify({"results": [{"id": m.id, "text": m.name, "name": m.name}
                               for m in models]})


@bp.route("/vehicle-details/<int:vehicle_id>")
@login_required
def get_vehicle_details(vehicle_id):
    """Powers the Maintenance Order form's Vehicle Info panel — same
    fields shown in the print report header (Branch, Current Assignee +
    Position), refreshed dynamically whenever the vehicle selection
    changes, not just on the initial pre-filled page load."""
    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().get(vehicle_id)
    if vehicle is None:
        return jsonify({"found": False})
    driver = vehicle.assigned_driver
    return jsonify({
        "found": True,
        "plate": vehicle.plate_number or vehicle.conduction_number,
        "brand_model": f"{vehicle.brand} {vehicle.model}",
        "branch": vehicle.branch.name if vehicle.branch else "—",
        "assignee": driver.full_name if driver else "—",
        "position": (driver.job_title or "—") if driver else "—",
        "odometer": vehicle.current_odometer,
    })


@bp.route("/pm-scope-template-details/<int:template_id>")
@login_required
def get_pm_scope_template_details(template_id):
    """Powers the collapsed-by-default 'Scope Details' preview on the
    Maintenance Order form — so a fleet manager can see exactly what
    activities a PM Scope Template includes before saving, not just its
    name."""
    from app.modules.maintenance_config.service import PMScopeTemplateService
    tmpl = PMScopeTemplateService().get_by_id(template_id)
    if tmpl is None:
        return jsonify({"found": False})
    items = sorted(tmpl.items, key=lambda i: i.sort_order)
    return jsonify({
        "found": True,
        "name": tmpl.name,
        "items": [{
            "sort_order": i.sort_order,
            "activity_code": i.activity_code,
            "activity_description": i.activity_description,
            "standard_labor_hours": (str(i.standard_labor_hours)
                                    if i.standard_labor_hours else None),
            "required_parts": i.required_parts,
        } for i in items],
    })


@bp.route("/pm-scope-templates-for-vehicle")
@login_required
def search_pm_scope_templates_for_vehicle():
    """Cascading PM Scope Template list for the Maintenance Order form —
    filtered to only what actually applies to the selected Vehicle (by
    Brand/Model, then Vehicle Type, then global — same precedence as the
    Dashboard's due-status calculation), not the entire global list.
    Fixes the reported bug where an unrelated vehicle's checklist showed
    up as a selectable option."""
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.maintenance_config.service import PMScopeTemplateService
    vehicle_id = request.args.get("vehicle_id")
    maintenance_type_id = request.args.get("maintenance_type_id")
    if not vehicle_id:
        return jsonify({"results": [], "due_template_id": None})

    vehicle = VehicleService().get(int(vehicle_id))
    if vehicle is None:
        return jsonify({"results": [], "due_template_id": None})

    mt_id = int(maintenance_type_id) if maintenance_type_id else None
    templates = PMScopeTemplateService().list_applicable_for_vehicle(
        vehicle, maintenance_type_id=mt_id)
    due_template = PMScopeTemplateService().get_next_due_scope_template(
        vehicle, maintenance_type_id=mt_id)
    return jsonify({
        "results": [{"id": t.id, "text": t.name} for t in templates],
        "due_template_id": due_template.id if due_template else None,
    })
