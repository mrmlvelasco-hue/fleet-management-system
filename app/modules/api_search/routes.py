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
from flask_login import login_required

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
        return f"{obj.employee_number} — {obj.last_name}, {obj.first_name}"


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
    return jsonify(VehicleSearchService().to_select2_response(**_search_params()))


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
    return jsonify(VehicleSearchService().to_table_response(**params))


@bp.route("/drivers")
@login_required
def search_drivers():
    return jsonify(DriverSearchService().to_select2_response(**_search_params()))


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
