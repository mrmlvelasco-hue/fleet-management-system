"""Vehicle Checklist service — score, submit, default template, MO hook."""
from datetime import date, datetime, time
from decimal import Decimal

from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService, NoSchemeError
from app.modules.transactions.vehicle_checklist.models import (
    ChecklistTemplate, ChecklistCategory, ChecklistItem,
    VehicleChecklist, VehicleChecklistLine, ChecklistDefect,
)


DEFAULT_CATEGORIES = [
    ("Exterior", [
        ("Headlights", False), ("Signal Lights", False), ("Brake Lights", True),
        ("Tires", True), ("Windshield", False), ("Mirrors", False),
        ("Wipers", False), ("Body Condition", False),
    ]),
    ("Engine / Mechanical", [
        ("Engine Oil", False), ("Coolant", False), ("Battery", False),
        ("Belts", False), ("Brake Fluid", True), ("Transmission Fluid", False),
    ]),
    ("Interior", [
        ("Dashboard", False), ("Horn", False), ("Air Conditioning", False),
        ("Seat Belts", True), ("Interior Lights", False),
    ]),
    ("Safety Equipment", [
        ("Fire Extinguisher", True), ("Warning Triangle", False),
        ("First Aid Kit", False), ("Spare Tire", False), ("Jack", False),
        ("Emergency Tools", False),
    ]),
    ("Documents", [
        ("OR", False), ("CR", False), ("Insurance", False),
        ("Registration", False),
    ]),
]


class ChecklistError(Exception):
    pass


class VehicleChecklistService:
    document_type_code = "CHK"

    def _assign_number(self, cl):
        if getattr(cl, "document_number", None):
            return cl
        try:
            cl.document_number = AutoNumberingService().generate(
                self.document_type_code)
        except Exception:
            year = date.today().year
            cl.document_number = f"CHK-{year}-{cl.id:06d}" if cl.id else None
        return cl

    def ensure_default_template(self):
        existing = ChecklistTemplate.query.filter_by(is_active=True).first()
        if existing:
            return existing
        tmpl = ChecklistTemplate(
            name="Daily Pre-Trip Inspection",
            description="Standard daily / pre-trip vehicle inspection",
            status="ACTIVE")
        db.session.add(tmpl)
        db.session.flush()
        for ci, (cat_name, items) in enumerate(DEFAULT_CATEGORIES):
            cat = ChecklistCategory(
                template_id=tmpl.id, name=cat_name, sort_order=ci)
            db.session.add(cat)
            db.session.flush()
            for ii, (item_name, safety) in enumerate(items):
                db.session.add(ChecklistItem(
                    category_id=cat.id, name=item_name, required=True,
                    is_safety=safety, maintenance_allowed=True,
                    photo_required_on_fail=safety, sort_order=ii))
        db.session.commit()
        return tmpl

    def list_templates(self):
        self.ensure_default_template()
        return (ChecklistTemplate.query
                .filter_by(is_active=True)
                .order_by(ChecklistTemplate.name)
                .all())

    def get_template(self, tid):
        return db.session.get(ChecklistTemplate, tid)

    def create_template(self, *, name, description=None, vehicle_type_id=None):
        t = ChecklistTemplate(name=name, description=description,
                              vehicle_type_id=vehicle_type_id or None,
                              status="ACTIVE")
        db.session.add(t)
        db.session.commit()
        return t

    def add_category(self, template_id, name, sort_order=0):
        c = ChecklistCategory(template_id=template_id, name=name,
                              sort_order=sort_order)
        db.session.add(c)
        db.session.commit()
        return c

    def add_item(self, category_id, *, name, required=True, is_safety=False,
                 photo_required_on_fail=False, maintenance_allowed=True,
                 sort_order=0):
        it = ChecklistItem(
            category_id=category_id, name=name, required=required,
            is_safety=is_safety, photo_required_on_fail=photo_required_on_fail,
            maintenance_allowed=maintenance_allowed, sort_order=sort_order)
        db.session.add(it)
        db.session.commit()
        return it

    def list_checklists(self, *, branch_id=None, vehicle_id=None, result=None,
                        status=None, date_from=None, date_to=None,
                        page=1, per_page=25):
        q = VehicleChecklist.query
        if branch_id:
            q = q.filter_by(branch_id=branch_id)
        if vehicle_id:
            q = q.filter_by(vehicle_id=vehicle_id)
        if result:
            q = q.filter_by(result=result)
        if status:
            q = q.filter_by(status=status)
        if date_from:
            q = q.filter(VehicleChecklist.inspection_date >= date_from)
        if date_to:
            q = q.filter(VehicleChecklist.inspection_date <= date_to)
        q = q.order_by(VehicleChecklist.id.desc())
        total = q.count()
        rows = q.offset((page - 1) * per_page).limit(per_page).all()
        return rows, total

    def get(self, cid):
        cl = db.session.get(VehicleChecklist, cid)
        if cl is not None and not cl.document_number:
            self._assign_number(cl)
            db.session.commit()
        return cl

    def create(self, *, vehicle_id, template_id, user, driver_id=None,
               odometer=None, inspection_date=None, inspection_time=None,
               remarks=None):
        from app.modules.master_data.vehicle.models import Vehicle
        vehicle = db.session.get(Vehicle, vehicle_id)
        if vehicle is None:
            raise ChecklistError("Vehicle not found.")
        tmpl = db.session.get(ChecklistTemplate, template_id)
        if tmpl is None or tmpl.status != "ACTIVE":
            raise ChecklistError("Checklist template is not available.")
        if odometer is not None and vehicle.current_odometer is not None:
            if int(odometer) < int(vehicle.current_odometer):
                raise ChecklistError(
                    f"Odometer {odometer} is below the vehicle's current "
                    f"reading ({vehicle.current_odometer}).")
        insp_date = inspection_date or date.today()
        insp_time = inspection_time
        if isinstance(insp_time, str) and insp_time:
            parts = insp_time.split(":")
            insp_time = time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        elif not insp_time:
            insp_time = datetime.now().time().replace(second=0, microsecond=0)

        cl = VehicleChecklist(
            template_id=tmpl.id,
            vehicle_id=vehicle.id,
            driver_id=driver_id or vehicle.assigned_driver_id,
            branch_id=vehicle.branch_id,
            odometer=odometer if odometer is not None else vehicle.current_odometer,
            inspection_date=insp_date,
            inspection_time=insp_time,
            status="DRAFT",
            remarks=remarks,
            created_by=user.id if user else None,
        )
        self._assign_number(cl)
        db.session.add(cl)
        db.session.flush()
        if not cl.document_number:
            self._assign_number(cl)
        order = 0
        for cat in tmpl.categories:
            for item in cat.items:
                if item.status != "ACTIVE":
                    continue
                db.session.add(VehicleChecklistLine(
                    checklist_id=cl.id,
                    item_id=item.id,
                    category_name=cat.name,
                    item_name=item.name,
                    is_required=item.required,
                    is_safety=item.is_safety,
                    sort_order=order,
                ))
                order += 1
        db.session.commit()
        return cl

    def save_draft(self, cid, *, responses, defects, odometer=None,
                   driver_id=None, remarks=None):
        cl = self.get(cid)
        if cl is None:
            raise ChecklistError("Checklist not found.")
        if cl.status != "DRAFT":
            raise ChecklistError("Only a draft can be edited.")
        if odometer is not None:
            cl.odometer = odometer
        if driver_id is not None:
            cl.driver_id = driver_id or None
        if remarks is not None:
            cl.remarks = remarks
        by_id = {ln.id: ln for ln in cl.lines}
        for row in responses or []:
            ln = by_id.get(int(row["line_id"]))
            if ln is None:
                continue
            resp = (row.get("response") or "").upper() or None
            if resp and resp not in ("PASS", "ATTENTION", "FAILED", "N/A"):
                raise ChecklistError(f"Invalid response '{resp}'.")
            ln.response = resp
        # Rebuild defects from payload (draft only).
        for d in list(cl.defects):
            db.session.delete(d)
        db.session.flush()
        for d in defects or []:
            if not d.get("line_id"):
                continue
            ln = by_id.get(int(d["line_id"]))
            if ln is None:
                continue
            db.session.add(ChecklistDefect(
                checklist_id=cl.id,
                line_id=ln.id,
                item_name=ln.item_name,
                observation=d.get("observation"),
                severity=(d.get("severity") or "MINOR").upper(),
                create_maintenance=bool(d.get("create_maintenance")),
            ))
        preview = self._score(cl)
        cl.score = preview["score"]
        cl.result = preview["result"]
        cl.applicable_count = preview["applicable"]
        cl.pass_count = preview["pass"]
        cl.attention_count = preview["attention"]
        cl.fail_count = preview["fail"]
        db.session.commit()
        return cl

    def _score(self, cl):
        applicable = pass_n = att = fail = 0
        critical_fail = False
        for ln in cl.lines:
            if not ln.response or ln.response == "N/A":
                continue
            applicable += 1
            if ln.response == "PASS":
                pass_n += 1
            elif ln.response == "ATTENTION":
                att += 1
            elif ln.response == "FAILED":
                fail += 1
                if ln.is_safety:
                    critical_fail = True
        score = Decimal("100.00") if applicable == 0 else (
            Decimal(pass_n) / Decimal(applicable) * Decimal("100")
        ).quantize(Decimal("0.01"))
        if critical_fail or score < 70:
            result = "FAILED"
        elif score < 90 or att > 0 or fail > 0:
            result = "ATTENTION"
        else:
            result = "PASS"
        return {
            "score": score, "result": result, "applicable": applicable,
            "pass": pass_n, "attention": att, "fail": fail,
            "critical_fail": critical_fail,
        }

    def submit(self, cid, user):
        cl = self.get(cid)
        if cl is None:
            raise ChecklistError("Checklist not found.")
        if cl.status != "DRAFT":
            raise ChecklistError("This checklist is already submitted.")
        for ln in cl.lines:
            if ln.is_required and not ln.response:
                raise ChecklistError(
                    f"'{ln.item_name}' is required before submit.")
            if ln.response in ("ATTENTION", "FAILED"):
                has = any(d.line_id == ln.id and d.observation
                          for d in cl.defects)
                if not has:
                    raise ChecklistError(
                        f"Add an observation for '{ln.item_name}'.")
        scored = self._score(cl)
        cl.score = scored["score"]
        cl.result = scored["result"]
        cl.applicable_count = scored["applicable"]
        cl.pass_count = scored["pass"]
        cl.attention_count = scored["attention"]
        cl.fail_count = scored["fail"]
        self._assign_number(cl)
        cl.status = "SUBMITTED"
        cl.submitted_at = datetime.utcnow()
        cl.submitted_by = user.id if user else None
        if cl.odometer and cl.vehicle and (
                cl.vehicle.current_odometer is None
                or cl.odometer > cl.vehicle.current_odometer):
            cl.vehicle.current_odometer = cl.odometer
        created_mos = []
        for d in cl.defects:
            if d.create_maintenance:
                mo = self._open_maintenance(cl, d, user)
                if mo is not None:
                    d.maintenance_order_id = mo.id
                    created_mos.append(mo.document_number)
        db.session.commit()
        return cl, created_mos

    def _open_maintenance(self, cl, defect, user):
        try:
            from app.modules.transactions.maintenance_order.service import (
                MaintenanceOrderService)
            desc = (
                f"Checklist {cl.document_number or cl.id}: "
                f"{defect.item_name} — {defect.observation or defect.severity}"
            )
            return MaintenanceOrderService().create(
                vehicle_id=cl.vehicle_id,
                scheduled_date=cl.inspection_date or date.today(),
                user=user,
                order_category="MAINTENANCE",
                description=desc,
                odometer_at_service=cl.odometer,
                driver_id=cl.driver_id,
            )
        except Exception:
            return None

    def dashboard(self, today=None):
        today = today or date.today()
        q = VehicleChecklist.query.filter(
            VehicleChecklist.status == "SUBMITTED")
        total = q.count()
        today_q = q.filter(VehicleChecklist.inspection_date == today)
        today_n = today_q.count()
        passed = q.filter_by(result="PASS").count()
        attention = q.filter_by(result="ATTENTION").count()
        failed = q.filter_by(result="FAILED").count()
        rate = 0 if total == 0 else round(passed / total * 100, 1)
        recent = (VehicleChecklist.query
                  .order_by(VehicleChecklist.id.desc())
                  .limit(10).all())
        return {
            "total": total,
            "completed_today": today_n,
            "passed": passed,
            "attention": attention,
            "failed": failed,
            "compliance_rate": rate,
            "recent": recent,
        }

    def defect_summary(self):
        from sqlalchemy import func
        rows = (db.session.query(
                    ChecklistDefect.item_name, func.count(ChecklistDefect.id))
                .group_by(ChecklistDefect.item_name)
                .order_by(func.count(ChecklistDefect.id).desc())
                .limit(12).all())
        open_n = ChecklistDefect.query.filter_by(status="OPEN").count()
        critical = ChecklistDefect.query.filter_by(severity="CRITICAL").count()
        major = ChecklistDefect.query.filter_by(severity="MAJOR").count()
        with_mo = ChecklistDefect.query.filter(
            ChecklistDefect.maintenance_order_id.isnot(None)).count()
        return {
            "common": [{"item": n, "count": c} for n, c in rows],
            "open": open_n, "critical": critical, "major": major,
            "with_maintenance": with_mo,
        }
