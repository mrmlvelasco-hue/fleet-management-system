"""Copy one vehicle model's PMS to another model.

"The Triton has the same PMS as the Strada" is a routine request, and
until now the only way to act on it was to rebuild the Strada's rows in
Excel under the new model name and upload them. The one file built
that way had the checklist sheet under the wrong name, 982 checklist
lines still filed under the old model, and the old model's profile
codes -- three different ways to end up with a wrong catalog.

This does the same job on the server, where none of that can happen:

  * every ACTIVE PM package of the source model is copied, with its
    intervals, alert settings and checklist, line for line;
  * the copies are linked to the target model by id;
  * each source PMS Profile becomes a NEW profile with its own code
    (BRAND-MODEL-MAINTENANCETYPE-N, the convention
    PMScheduleService.backfill_missing_profile_codes already uses), so
    the two models never share a profile;
  * the source model is not touched.

A target that already has active PMS is refused rather than added to:
two overlapping sets of packages for one model is never what was
meant, and it is what a second click on Copy would otherwise produce.
"""
import re

from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.modules.maintenance_config.models import (
    PMSchedule, PMScopeItem, PMScopeTemplate)
from app.modules.master_data.vehicle_brand.models import VehicleModel

# pm_schedules.profile_code is String(40) and pm_scope_items.
# activity_code (profile code + "-NNN") is String(40) too, so the
# generated code is held to 36 characters: 32 for the name part plus
# "-" and up to three digits of sequence number.
_CODE_BASE_MAX = 32

_SCHEDULE_COPY_FIELDS = (
    "vehicle_type_id", "variant", "engine_type", "fuel_type",
    "transmission", "model_year_from", "model_year_to", "effective_date",
    "sequence_position", "maintenance_type_id", "trigger_mode",
    "interval_km", "interval_days", "interval_hours", "cumulative_km",
    "priority", "work_description_template", "notify_before_km",
    "notify_before_days", "escalate_if_overdue", "next_pms_generation",
    "next_due_calculation_method",
)

_ITEM_COPY_FIELDS = (
    "activity_description", "standard_labor_hours", "estimated_cost",
    "required_parts", "vendor_recommendation", "sort_order",
)


class PMSCopyError(ValueError):
    """A copy that cannot go ahead, with a message fit to show as is."""

    def __init__(self, message, field="_"):
        super().__init__(message)
        self.field = field


def _slug(text):
    cleaned = "".join(c if c.isalnum() else "-" for c in (text or "").upper())
    return re.sub(r"-+", "-", cleaned).strip("-")


def _label(model):
    return f"{model.brand.name} {model.name}"


class PMSCopyService:

    def copy_to_model(self, *, source_model_id, target_model_id,
                      dry_run=True) -> dict:
        source = self._model(source_model_id, "source_model_id",
                             "Choose the model to copy from.")
        target = self._model(target_model_id, "target_model_id",
                             "Choose the model to copy to.")
        if source.id == target.id:
            raise PMSCopyError(
                "Choose two different models: the PMS cannot be copied "
                "onto the model it comes from.", "target_model_id")

        schedules = self._schedules_of(source)
        if not schedules:
            raise PMSCopyError(
                f"{_label(source)} has no active PMS to copy.",
                "source_model_id")

        already = self._schedules_of(target)
        if already:
            profiles = sorted({s.profile_code for s in already
                               if s.profile_code})
            listed = f" ({', '.join(profiles[:5])})" if profiles else ""
            raise PMSCopyError(
                f"{_label(target)} already has {len(already)} active PM "
                f"package(s){listed}. Deactivate them first if they "
                f"should be replaced -- copying on top would leave the "
                f"model with two overlapping sets.", "target_model_id")

        renames = self._renames(source, target)
        used_codes = {code for (code,) in db.session.query(
            PMSchedule.profile_code).filter(
            PMSchedule.profile_code.isnot(None)).distinct()}

        # One source profile -> one new profile. Packages with no
        # profile code at all are grouped by maintenance type, which is
        # how they would have been presented as a series.
        groups = {}
        for sched in schedules:
            key = (sched.profile_code if sched.profile_code
                   else ("", sched.maintenance_type_id))
            groups.setdefault(key, []).append(sched)

        result = {
            "dry_run": bool(dry_run),
            "source": {"model_id": source.id, "brand": source.brand.name,
                       "model": source.name},
            "target": {"model_id": target.id, "brand": target.brand.name,
                       "model": target.name},
            "profiles": [],
            "packages_created": 0,
            "scope_templates_created": 0,
            "scope_items_created": 0,
        }

        for group in groups.values():
            first = group[0]
            new_code = self._new_code(target, first, used_codes)
            used_codes.add(new_code)
            checklist_items = 0

            for sched in group:
                copy = PMSchedule(
                    vehicle_brand_id=target.brand_id,
                    vehicle_model_id=target.id,
                    vehicle_make=None, vehicle_model=None,
                    profile_code=new_code,
                    profile_description=self._rename(
                        sched.profile_description, renames, 255),
                    **{f: getattr(sched, f) for f in _SCHEDULE_COPY_FIELDS})
                if not dry_run:
                    db.session.add(copy)

                for template in sched.scope_templates:
                    if not template.is_active:
                        continue
                    items = [i for i in template.items if i.is_active]
                    result["scope_templates_created"] += 1
                    checklist_items += len(items)
                    if dry_run:
                        continue
                    template_copy = PMScopeTemplate(
                        maintenance_type_id=template.maintenance_type_id,
                        name=self._rename(template.name, renames, 120),
                        description=self._rename(
                            template.description, renames, 255),
                        pm_schedule=copy)
                    for position, item in enumerate(items, start=1):
                        template_copy.items.append(PMScopeItem(
                            activity_code=self._activity_code(
                                item.activity_code, sched.profile_code,
                                new_code, position),
                            **{f: getattr(item, f)
                               for f in _ITEM_COPY_FIELDS}))
                    db.session.add(template_copy)

            result["packages_created"] += len(group)
            result["scope_items_created"] += checklist_items
            result["profiles"].append({
                "source_profile_code": first.profile_code,
                "new_profile_code": new_code,
                "maintenance_type": (first.maintenance_type.name
                                     if first.maintenance_type else None),
                "description": self._rename(
                    first.profile_description, renames, 255),
                "packages": len(group),
                "checklist_items": checklist_items,
            })

        if not dry_run:
            # One transaction: the target ends up with the whole PMS or
            # none of it.
            db.session.commit()
        result["profiles"].sort(key=lambda p: p["new_profile_code"])
        return result

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _model(model_id, field, missing_message):
        try:
            model_id = int(model_id)
        except (TypeError, ValueError):
            raise PMSCopyError(missing_message, field)
        model = (VehicleModel.query
                 .options(joinedload(VehicleModel.brand))
                 .filter_by(id=model_id, is_active=True).first())
        if model is None or model.brand is None:
            raise PMSCopyError(
                "That model is not in Vehicle Models (or is inactive).",
                field)
        return model

    @staticmethod
    def _schedules_of(model):
        """Active PM packages belonging to `model`: linked to it by id,
        or -- for PMS imported before the model existed in Vehicle
        Models -- stored under its brand and model name as text."""
        brand_name = model.brand.name.strip().lower()
        model_name = model.name.strip().lower()
        rows = (PMSchedule.query
                .filter(PMSchedule.is_active.is_(True))
                .filter(db.or_(
                    PMSchedule.vehicle_model_id == model.id,
                    db.and_(
                        PMSchedule.vehicle_model_id.is_(None),
                        db.func.lower(db.func.trim(PMSchedule.vehicle_make))
                        == brand_name,
                        db.func.lower(db.func.trim(PMSchedule.vehicle_model))
                        == model_name)))
                .options(joinedload(PMSchedule.maintenance_type),
                         selectinload(PMSchedule.scope_templates)
                         .selectinload(PMScopeTemplate.items))
                .all())
        return sorted(rows, key=lambda r: (
            r.profile_code or "", r.sequence_position is None,
            r.sequence_position or 0, r.id))

    @staticmethod
    def _renames(source, target):
        """Text to swap so a copied name reads as the target model's:
        'Brand Model' together first (so a copy to another brand reads
        'Toyota Hilux', not 'Mitsubishi Hilux'), then the model name on
        its own."""
        return [(_label(source), _label(target)),
                (source.name, target.name)]

    @staticmethod
    def _rename(text, renames, max_length):
        if not text:
            return text
        for old, new in renames:
            text = re.sub(
                r"(?<![A-Za-z0-9])" + re.escape(old) + r"(?![A-Za-z0-9])",
                lambda _m, new=new: new, text, flags=re.IGNORECASE)
        return text[:max_length]

    @staticmethod
    def _new_code(target, schedule, used_codes):
        mtype = _slug(schedule.maintenance_type.code
                      if schedule.maintenance_type else "") or "PM"
        vehicle = "-".join(filter(None, [_slug(target.brand.name),
                                         _slug(target.name)])) or "VEHICLE"
        room = max(_CODE_BASE_MAX - len(mtype) - 1, 8)
        base = f"{vehicle[:room].rstrip('-')}-{mtype}"[:_CODE_BASE_MAX]
        n = 1
        while f"{base}-{n}" in used_codes:
            n += 1
        return f"{base}-{n}"

    @staticmethod
    def _activity_code(original, source_code, new_code, position):
        """Keep the line's own numbering when it follows the usual
        '<profile code>-NNN' shape; otherwise number it by position."""
        if original and source_code and original.startswith(f"{source_code}-"):
            code = f"{new_code}{original[len(source_code):]}"
            if len(code) <= 40:
                return code
        if original is None:
            return None
        return f"{new_code}-{position:03d}"[:40]
