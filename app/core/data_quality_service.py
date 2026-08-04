"""Fleet Data Quality Scorecard.

Measures how completely the Vehicle Master is filled in, per vehicle and
per branch.

Deliberately NOT hardcoded: which fields count, whether each is
mandatory, and what each is worth are all rows in `data_quality_fields`,
maintained by a fleet administrator in System Administration. Adding a
column to Vehicle later surfaces it in the settings screen automatically
(see sync_fields) rather than needing a code change -- the same
"configuration, not code" rule the approval matrix and transaction-type
classification already follow in this system.

Placeholder handling matters here. The importer already strips values
like "N/A", "No CR" and "0000000" to real NULLs, because a scorecard
that counts "N/A" as a filled field reports a completeness figure that
looks good and means nothing.
"""
from sqlalchemy import func

from app.extensions import db
from app.core.models.base import BaseModel
from app.core.request_cache import request_cached


# Plumbing, not business data -- these should never appear as something
# an administrator could weight, because they are always populated and
# would only inflate every score.
EXCLUDED_FIELDS = {
    "id", "created_at", "updated_at", "created_by", "updated_by",
    "is_active",
}

# A readable label for each field, so the settings screen doesn't show
# raw column names. Anything not listed here is title-cased from its
# column name, so a newly added column is still presentable.
FIELD_LABELS = {
    "plate_number": "Plate Number",
    "conduction_number": "Conduction Number",
    "chassis_number": "Chassis Number",
    "engine_number": "Engine Number",
    "vehicle_type_id": "Vehicle Type",
    "branch_id": "Branch",
    "department_id": "Department",
    "business_unit_id": "Business Unit",
    "assigned_driver_id": "Assigned Driver",
    "pm_schedule_id": "PM Schedule",
    "far_number": "FAR Number",
    "cr_number": "CR Number",
    "mv_file_number": "MV File Number",
    "lto_office": "LTO Office",
    "last_known_registration_expiry": "Last Known Registration Expiry",
    "ctpl_policy_number": "CTPL Policy Number",
    "ctpl_insurance_provider": "CTPL Insurance Provider",
    "comprehensive_policy_number": "Comprehensive Policy Number",
    "comprehensive_insurance_provider": "Comprehensive Insurance Provider",
    "insurance_reference_number": "Insurance Reference Number",
    "assured_value_current_year": "Assured Value (Current Year)",
    "top_up_amount": "Top-up Amount",
    "last_pm_odometer": "Last PM Odometer",
    "last_pm_date": "Last PM Date",
    "mr_eds": "MR / EDS",
}

# Sensible grouping for the settings screen, so 60+ fields aren't one
# undifferentiated list.
FIELD_GROUPS = {
    "IDENTITY": ("plate_number", "conduction_number", "chassis_number",
                "engine_number", "brand", "model", "year", "variant",
                "color", "vehicle_type_id", "vehicle_body_type"),
    "TECHNICAL": ("engine_type", "transmission", "fuel_type",
                 "displacement", "component_group", "current_odometer",
                 "current_engine_hours"),
    "ORGANISATION": ("branch_id", "department_id", "business_unit_id",
                    "assigned_driver_id", "assignment",
                    "assignment_group_classification", "vehicle_usage"),
    "ACQUISITION": ("acquisition_date", "acquisition_cost", "supplier",
                   "leasing_company", "delivery_date", "start_date",
                   "end_date", "with_vehicle_contract", "top_up_amount"),
    "REGISTRATION": ("far_number", "cr_number", "mv_file_number",
                    "lto_office", "last_known_registration_expiry"),
    "INSURANCE": ("has_ctpl", "ctpl_policy_number", "ctpl_from_date",
                 "ctpl_to_date", "ctpl_insurance_provider",
                 "comprehensive_policy_number",
                 "comprehensive_insurance_provider",
                 "insurance_reference_number",
                 "assured_value_current_year",
                 "has_od_theft_aon", "od_theft_aon_from_date",
                 "od_theft_aon_to_date", "has_vtpl_pd",
                 "vtpl_pd_from_date", "vtpl_pd_to_date", "has_vtpl_bi",
                 "vtpl_bi_from_date", "vtpl_bi_to_date",
                 "has_inland_marine"),
    "MAINTENANCE": ("pm_schedule_id", "last_pm_odometer", "last_pm_date"),
    "OTHER": ("status", "remarks", "notes", "mr_eds"),
}


def group_of(field_name):
    for group, names in FIELD_GROUPS.items():
        if field_name in names:
            return group
    return "OTHER"


def label_of(field_name):
    if field_name in FIELD_LABELS:
        return FIELD_LABELS[field_name]
    return field_name.replace("_id", "").replace("_", " ").title()


class DataQualityField(db.Model, BaseModel):
    """One configurable row per Vehicle field.

    `is_required` marks a field the business considers mandatory -- it
    drives the "missing required fields" alerting. `include_in_score`
    decides whether it affects the percentage at all, which is a
    separate decision: a field can be worth tracking without being
    mandatory, and vice versa.
    """
    __tablename__ = "data_quality_fields"

    field_name = db.Column(db.String(80), nullable=False, unique=True)
    label = db.Column(db.String(120), nullable=False)
    field_group = db.Column(db.String(30), nullable=False, default="OTHER")
    is_required = db.Column(db.Boolean, default=False, nullable=False)
    include_in_score = db.Column(db.Boolean, default=True, nullable=False)
    weight = db.Column(db.Integer, default=5, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class DataQualityService:

    # Branch-level bands, per the specification. Held here rather than in
    # the template so the dashboard, the branch table and any export all
    # colour the same number identically.
    THRESHOLD_EXCELLENT = 90
    THRESHOLD_WARNING = 70

    def sync_fields(self):
        """Make sure every eligible Vehicle column has a settings row.

        Called from `flask seed all`. Adding a column to Vehicle later
        therefore surfaces it in the settings screen on the next seed,
        instead of silently never being scoreable.

        Only ever INSERTS. An administrator's choices about an existing
        field are never overwritten -- re-running seed must not quietly
        undo their configuration.
        """
        from app.modules.master_data.vehicle.models import Vehicle

        existing = {f.field_name for f in DataQualityField.query.all()}
        created = 0
        for index, column in enumerate(Vehicle.__table__.columns):
            name = column.name
            if name in EXCLUDED_FIELDS or name in existing:
                continue
            db.session.add(DataQualityField(
                field_name=name, label=label_of(name),
                field_group=group_of(name),
                # Everything starts INCLUDED, as asked -- the fleet
                # administrator then turns off what doesn't apply, which
                # is easier than hunting for what to turn on. Nothing
                # starts REQUIRED, because declaring a field mandatory is
                # a business decision that should be made deliberately,
                # not inherited from a default.
                is_required=False, include_in_score=True,
                weight=5, sort_order=index))
            created += 1
        return created

    def scored_fields(self):
        return (DataQualityField.query
               .filter_by(include_in_score=True, is_active=True)
               .order_by(DataQualityField.sort_order).all())

    @staticmethod
    def _is_filled(vehicle, field_name):
        """Whether a field counts as populated.

        Empty strings and whitespace count as MISSING: a space typed
        into a text box is not data, and treating it as filled is how a
        completeness score becomes flattering and useless. Booleans are
        deliberately always 'filled' -- False is a real answer, not an
        absence.
        """
        value = getattr(vehicle, field_name, None)
        if value is None:
            return False
        if isinstance(value, bool):
            return True
        if isinstance(value, str):
            return bool(value.strip())
        return True

    def score_vehicle(self, vehicle, fields=None):
        """Weighted completeness for one vehicle."""
        fields = fields if fields is not None else self.scored_fields()
        total_weight = sum(f.weight for f in fields)
        if not total_weight:
            return {"score": 0, "earned": 0, "possible": 0,
                   "missing": [], "missing_required": []}

        earned, missing, missing_required = 0, [], []
        for field in fields:
            if self._is_filled(vehicle, field.field_name):
                earned += field.weight
            else:
                missing.append(field)
                if field.is_required:
                    missing_required.append(field)

        return {
            "score": round(earned / total_weight * 100, 1),
            "earned": earned, "possible": total_weight,
            "missing": missing, "missing_required": missing_required,
        }

    def rating_of(self, score):
        if score >= self.THRESHOLD_EXCELLENT:
            return "EXCELLENT"
        if score >= self.THRESHOLD_WARNING:
            return "WARNING"
        return "CRITICAL"

    def _vehicles(self, user=None, branch_id=None):
        from app.modules.master_data.vehicle.models import Vehicle
        query = Vehicle.query.filter(Vehicle.is_active.is_(True),
                                    Vehicle.status != "DISPOSED")
        if branch_id:
            query = query.filter(Vehicle.branch_id == branch_id)
        return query.all()

    @request_cached("dq_branch_scorecard")
    def branch_scorecard(self, user=None) -> list:
        """Per-branch rollup, ranked worst-first.

        Worst-first because the point of the screen is to show where
        attention is needed; a ranking that opens with the branches
        already doing well buries the ones that aren't.
        """
        from app.modules.master_data.org.models import Branch

        fields = self.scored_fields()
        if not fields:
            return []

        branches = {b.id: b for b in Branch.query.all()}
        buckets = {}
        for vehicle in self._vehicles(user=user):
            result = self.score_vehicle(vehicle, fields)
            bucket = buckets.setdefault(
                vehicle.branch_id,
                {"branch": branches.get(vehicle.branch_id),
                 "vehicles": 0, "complete": 0, "incomplete": 0,
                 "score_sum": 0.0, "missing_points": 0})
            bucket["vehicles"] += 1
            bucket["score_sum"] += result["score"]
            bucket["missing_points"] += len(result["missing"])
            if result["missing"]:
                bucket["incomplete"] += 1
            else:
                bucket["complete"] += 1

        rows = []
        for branch_id, bucket in buckets.items():
            average = bucket["score_sum"] / bucket["vehicles"]
            rows.append({
                "branch": bucket["branch"],
                "branch_name": (bucket["branch"].name if bucket["branch"]
                               else "(No branch assigned)"),
                "vehicles": bucket["vehicles"],
                "complete": bucket["complete"],
                "incomplete": bucket["incomplete"],
                "missing_points": bucket["missing_points"],
                "score": round(average, 1),
                "rating": self.rating_of(average),
            })
        rows.sort(key=lambda r: r["score"])
        for position, row in enumerate(rows, start=1):
            row["rank"] = position
        return rows

    @request_cached("dq_field_completion")
    def field_completion(self, user=None) -> list:
        """Completion rate per field, worst first -- that ordering is
        what makes the list actionable."""
        fields = self.scored_fields()
        vehicles = self._vehicles(user=user)
        total = len(vehicles)
        if not total or not fields:
            return []

        rows = []
        for field in fields:
            filled = sum(1 for v in vehicles
                        if self._is_filled(v, field.field_name))
            rate = filled / total * 100
            rows.append({
                "field": field, "label": field.label,
                "group": field.field_group,
                "filled": filled, "missing": total - filled,
                "rate": round(rate, 1),
                "rating": self.rating_of(rate),
                "is_required": field.is_required,
            })
        rows.sort(key=lambda r: r["rate"])
        return rows

    @request_cached("dq_summary")
    def summary(self, user=None) -> dict:
        """Headline figures for the dashboard widget."""
        fields = self.scored_fields()
        vehicles = self._vehicles(user=user)
        if not fields or not vehicles:
            return {"score": 0, "rating": "CRITICAL", "vehicles": 0,
                   "incomplete": 0, "missing_points": 0,
                   "top": [], "bottom": [], "common_missing": []}

        total_score, incomplete, missing_points = 0.0, 0, 0
        missing_counter = {}
        for vehicle in vehicles:
            result = self.score_vehicle(vehicle, fields)
            total_score += result["score"]
            missing_points += len(result["missing"])
            if result["missing"]:
                incomplete += 1
            for field in result["missing"]:
                missing_counter[field.label] = missing_counter.get(
                    field.label, 0) + 1

        branches = self.branch_scorecard(user=user)
        overall = total_score / len(vehicles)
        common = sorted(missing_counter.items(), key=lambda kv: -kv[1])[:5]
        return {
            "score": round(overall, 1),
            "rating": self.rating_of(overall),
            "vehicles": len(vehicles),
            "incomplete": incomplete,
            "missing_points": missing_points,
            # branch_scorecard is worst-first, so the bottom 5 are its
            # head and the top 5 its tail reversed.
            "bottom": branches[:5],
            "top": list(reversed(branches[-5:])),
            "common_missing": [{"label": label, "count": count}
                              for label, count in common],
        }

    def vehicles_with_gaps(self, branch_id=None, user=None, limit=200):
        """Drill-down: Branch -> Vehicle -> Missing Fields."""
        fields = self.scored_fields()
        rows = []
        for vehicle in self._vehicles(user=user, branch_id=branch_id):
            result = self.score_vehicle(vehicle, fields)
            if not result["missing"]:
                continue
            rows.append({
                "vehicle": vehicle,
                "score": result["score"],
                "rating": self.rating_of(result["score"]),
                "missing": result["missing"],
                "missing_required": result["missing_required"],
            })
        rows.sort(key=lambda r: r["score"])
        return rows[:limit]
