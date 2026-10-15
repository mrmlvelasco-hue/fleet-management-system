"""Maintenance Order "Tire & Battery Work" (client decisions 2026-10-15).

The MO says WHAT happens to each tire position / the battery; completing
the MO turns that into Tire / Battery transactions, so tire history,
vehicle history and the print-out all come from one entry:

  PMS-TIRE / PMS-BATT order raised  -> lines PLANNED (pre-filled with what
                                       is on the vehicle now)
  mechanic records actuals          -> lines DONE (serial fitted, readings)
  MO completed                      -> post(): transactions COMPLETED,
                                       source MO, no second approval;
                                       new vendor-supplied items registered

Nothing physical happens before completion: a draft, rejected or
cancelled order never touches a tire or battery.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.modules.master_data.battery.models import Battery
from app.modules.master_data.tire.models import Tire
from app.modules.transactions.maintenance_order.models import (
    MaintenanceOrder, MaintenanceOrderEquipmentLine)

KINDS = ("TIRE", "BATTERY")
ACTIONS = {"TIRE": ("REPLACE", "ROTATE", "INSPECT", "REPAIR"),
           "BATTERY": ("REPLACE", "TEST")}
LINE_STATUSES = ("PLANNED", "DONE")
INSTALL_SOURCES = ("VENDOR", "STOCK")
TEST_RESULTS = ("GOOD", "WEAK", "REPLACE")
CLOSED_ORDER_STATUSES = ("COMPLETED", "CANCELLED", "REJECTED")
# Light-vehicle default when nothing on the vehicle is registered yet.
DEFAULT_TIRE_POSITIONS = ("FL", "FR", "RL", "RR")
# Maintenance types imported before equipment_kind existed.
LEGACY_TYPE_KINDS = {"PMS-TIRE": "TIRE", "PMS-BATT": "BATTERY"}


class EquipmentWorkError(ValueError):
    def __init__(self, message, field="_", line=None):
        super().__init__(message)
        self.field = field
        self.line = line


def _dec(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise EquipmentWorkError(f"'{v}' is not a number.")


def _int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise EquipmentWorkError(f"'{v}' is not a whole number.")


def _str(v, n=None):
    v = (str(v).strip() if v is not None else "") or None
    return v[:n] if (v and n) else v


class EquipmentConditionService:
    """Good / Watch / Replace for a tire, Good / Weak for a battery, from
    the System Parameters the administrator maintains."""

    def __init__(self):
        from app.modules.system_admin.services.system_parameter_service \
            import SystemParameterService
        p = SystemParameterService()

        def num(code, default):
            try:
                return float(p.get(code, default=default))
            except (TypeError, ValueError):
                return float(default)
        self.tire_watch_mm = num("TIRE_TREAD_WATCH_MM", 4.0)
        self.tire_replace_mm = num("TIRE_TREAD_REPLACE_MM", 3.0)
        self.tire_max_age_months = int(num("TIRE_MAX_AGE_MONTHS", 60))
        self.battery_low_voltage = num("BATTERY_LOW_VOLTAGE", 12.4)
        self.battery_warranty_warn_days = int(
            num("BATTERY_WARRANTY_WARN_DAYS", 30))

    def thresholds(self):
        return {"tire_watch_mm": self.tire_watch_mm,
                "tire_replace_mm": self.tire_replace_mm,
                "tire_max_age_months": self.tire_max_age_months,
                "battery_low_voltage": self.battery_low_voltage,
                "battery_warranty_warn_days": self.battery_warranty_warn_days}

    def tire(self, tire, today=None):
        """GOOD | WATCH | REPLACE | None (nothing measured yet)."""
        from datetime import date
        today = today or date.today()
        if tire is None:
            return None
        if tire.mounted_date:
            months = ((today.year - tire.mounted_date.year) * 12
                      + today.month - tire.mounted_date.month)
            if months >= self.tire_max_age_months:
                return "REPLACE"
        if tire.current_tread_mm is None:
            return None
        mm = float(tire.current_tread_mm)
        if mm <= self.tire_replace_mm:
            return "REPLACE"
        if mm <= self.tire_watch_mm:
            return "WATCH"
        return "GOOD"

    def battery(self, battery):
        if battery is None or battery.last_voltage is None:
            return None
        return ("WEAK" if float(battery.last_voltage)
                <= self.battery_low_voltage else "GOOD")


class EquipmentWorkService:

    # ── Reading ────────────────────────────────────────────────────────
    @staticmethod
    def kind_for_type(mtype):
        if mtype is None:
            return None
        return (getattr(mtype, "equipment_kind", None)
                or LEGACY_TYPE_KINDS.get((mtype.code or "").upper()))

    def lines(self, order_id):
        return (MaintenanceOrderEquipmentLine.query
                .filter_by(order_id=order_id)
                .order_by(MaintenanceOrderEquipmentLine.sort_order,
                          MaintenanceOrderEquipmentLine.id).all())

    @staticmethod
    def tire_label(t):
        bits = [t.serial_number, " ".join(x for x in (t.brand, t.model) if x),
                t.size]
        return " · ".join(b for b in bits if b)

    @staticmethod
    def battery_label(b):
        bits = [b.serial_number, " ".join(x for x in (b.brand, b.model) if x)]
        return " · ".join(x for x in bits if x)

    def installed(self, vehicle_id):
        """What is on the vehicle now, per the masters."""
        cond = EquipmentConditionService()
        tires = (Tire.query.filter_by(vehicle_id=vehicle_id,
                                      status="MOUNTED").all())
        order = self._position_order()
        tires.sort(key=lambda t: order.get(t.wheel_position or "", 999))
        batteries = Battery.query.filter_by(vehicle_id=vehicle_id,
                                            status="MOUNTED").all()
        return {
            "tires": [self._tire_json(t, cond) for t in tires],
            "batteries": [self._battery_json(b, cond) for b in batteries],
            "thresholds": cond.thresholds(),
        }

    def _tire_json(self, t, cond):
        return {"id": t.id, "serial_number": t.serial_number,
                "brand": t.brand, "model": t.model, "size": t.size,
                "wheel_position": t.wheel_position,
                "current_tread_mm": (float(t.current_tread_mm)
                                     if t.current_tread_mm is not None
                                     else None),
                "mounted_date": (t.mounted_date.isoformat()
                                 if t.mounted_date else None),
                "mounted_odometer": t.mounted_odometer,
                "label": self.tire_label(t), "condition": cond.tire(t)}

    def _battery_json(self, b, cond):
        return {"id": b.id, "serial_number": b.serial_number,
                "brand": b.brand, "model": b.model,
                "capacity_ah": b.capacity_ah, "voltage": b.voltage,
                "last_voltage": (float(b.last_voltage)
                                 if b.last_voltage is not None else None),
                "last_test_date": (b.last_test_date.isoformat()
                                   if b.last_test_date else None),
                "mounted_date": (b.mounted_date.isoformat()
                                 if b.mounted_date else None),
                "warranty_months": b.warranty_months,
                "label": self.battery_label(b),
                "condition": cond.battery(b)}

    @staticmethod
    def _position_order():
        """WHEEL_POSITION lookup sort order (admin-maintained)."""
        from app.modules.system_admin.services.lookup_service import (
            LookupService)
        rows = LookupService().get_by_type_with_fallback("WHEEL_POSITION")
        return {r.code: i for i, r in enumerate(rows)}

    def prefill(self, vehicle_id, maintenance_type_id):
        """Suggested lines for a new order: one per tire position (what is
        mounted there, or "not registered"), or the battery."""
        from app.modules.master_data.reference.models import MaintenanceType
        mtype = (db.session.get(MaintenanceType, maintenance_type_id)
                 if maintenance_type_id else None)
        kind = self.kind_for_type(mtype)
        cond = EquipmentConditionService()
        out = {"kind": kind, "lines": [], "thresholds": cond.thresholds()}
        if kind is None or not vehicle_id:
            return out
        if kind == "TIRE":
            mounted = {t.wheel_position: t for t in Tire.query.filter_by(
                vehicle_id=vehicle_id, status="MOUNTED").all()
                if t.wheel_position}
            order = self._position_order()
            positions = list(DEFAULT_TIRE_POSITIONS) + [
                p for p in mounted if p not in DEFAULT_TIRE_POSITIONS]
            positions.sort(key=lambda p: order.get(p, 999))
            for i, pos in enumerate(positions):
                t = mounted.get(pos)
                out["lines"].append({
                    "kind": "TIRE", "position": pos, "action": "REPLACE",
                    "status": "PLANNED", "sort_order": i,
                    "current_tire_id": t.id if t else None,
                    "current_label": self.tire_label(t) if t else None,
                    "current_tread_mm": (float(t.current_tread_mm)
                                         if t and t.current_tread_mm
                                         is not None else None),
                    "mounted_date": (t.mounted_date.isoformat()
                                     if t and t.mounted_date else None),
                    "not_registered": t is None,
                    "condition": cond.tire(t) if t else None,
                    # Spare is offered but not ticked by default.
                    "include": pos != "SPARE",
                })
        else:
            b = Battery.query.filter_by(vehicle_id=vehicle_id,
                                        status="MOUNTED").first()
            out["lines"].append({
                "kind": "BATTERY", "position": None, "action": "REPLACE",
                "status": "PLANNED", "sort_order": 0,
                "current_battery_id": b.id if b else None,
                "current_label": self.battery_label(b) if b else None,
                "last_voltage": (float(b.last_voltage)
                                 if b and b.last_voltage is not None
                                 else None),
                "mounted_date": (b.mounted_date.isoformat()
                                 if b and b.mounted_date else None),
                "not_registered": b is None,
                "condition": cond.battery(b) if b else None,
                "include": True,
            })
        return out

    # ── Writing ────────────────────────────────────────────────────────
    def replace_lines(self, order_id, rows, commit=True):
        """The order's Tire & Battery Work IS `rows` (same replace-the-set
        contract as the Scope of Work). Validated whole before anything is
        written."""
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise EquipmentWorkError("Maintenance order not found.")
        if order.status in CLOSED_ORDER_STATUSES:
            raise EquipmentWorkError(
                "This order is closed; its tire & battery work can no "
                "longer change.")
        rows = list(rows or [])
        if rows and order.order_category == "OPERATIONAL":
            raise EquipmentWorkError(
                "Operational orders have no tire & battery work.", "kind")
        clean = [self._normalize(order, r, i) for i, r in enumerate(rows)]
        for line in list(order.equipment_lines):
            db.session.delete(line)
        db.session.flush()
        for c in clean:
            db.session.add(MaintenanceOrderEquipmentLine(order_id=order.id,
                                                         **c))
        db.session.flush()
        db.session.expire(order, ["equipment_lines"])
        if commit:
            db.session.commit()
        return self.lines(order.id)

    def _normalize(self, order, r, i):
        def err(msg, field):
            return EquipmentWorkError(msg, field, line=i)

        kind = (_str(r.get("kind")) or "").upper()
        if kind not in KINDS:
            raise err("Kind must be TIRE or BATTERY.", "kind")
        action = (_str(r.get("action")) or "").upper()
        if action not in ACTIONS[kind]:
            raise err(f"{action or 'Blank'} is not a {kind.lower()} action. "
                      f"Use one of: {', '.join(ACTIONS[kind])}.", "action")
        position = (_str(r.get("position"), 20) or "").upper() or None
        if kind == "TIRE" and not position:
            raise err("Choose the wheel position.", "position")
        status = (_str(r.get("status")) or "PLANNED").upper()
        if status not in LINE_STATUSES:
            raise err("Status must be PLANNED or DONE.", "status")
        source = (_str(r.get("install_source")) or "").upper() or None
        if source and source not in INSTALL_SOURCES:
            raise err("Install source must be VENDOR or STOCK.",
                      "install_source")
        test_result = (_str(r.get("test_result")) or "").upper() or None
        if test_result and test_result not in TEST_RESULTS:
            raise err("Test result must be GOOD, WEAK or REPLACE.",
                      "test_result")
        c = dict(
            kind=kind, action=action,
            position=position if kind == "TIRE" else None,
            rotate_to_position=(_str(r.get("rotate_to_position"), 20)
                                or "").upper() or None,
            current_tire_id=_int(r.get("current_tire_id"))
            if kind == "TIRE" else None,
            current_battery_id=_int(r.get("current_battery_id"))
            if kind == "BATTERY" else None,
            current_description=_str(r.get("current_description"), 150),
            install_source=source,
            installed_tire_id=_int(r.get("installed_tire_id"))
            if kind == "TIRE" else None,
            installed_battery_id=_int(r.get("installed_battery_id"))
            if kind == "BATTERY" else None,
            new_serial=_str(r.get("new_serial"), 60),
            new_brand=_str(r.get("new_brand"), 80),
            new_model=_str(r.get("new_model"), 80),
            new_size=_str(r.get("new_size"), 30),
            new_warranty_months=_int(r.get("new_warranty_months")),
            tread_depth_mm=_dec(r.get("tread_depth_mm")),
            pressure_psi=_int(r.get("pressure_psi")),
            voltage=_dec(r.get("voltage")),
            test_result=test_result,
            status=status, remarks=_str(r.get("remarks"), 255),
            sort_order=_int(r.get("sort_order")) if r.get(
                "sort_order") not in (None, "") else i,
        )
        if status == "DONE":
            self._check_done(order, c, err)
        return c

    def _check_done(self, order, c, err):
        """A DONE line must say enough for posting to succeed."""
        kind, action = c["kind"], c["action"]
        if action == "ROTATE" and not c["rotate_to_position"]:
            raise err("Choose the wheel the tire was rotated to.",
                      "rotate_to_position")
        if action != "REPLACE":
            return
        if not c["install_source"]:
            raise err("Say where the new one came from (vendor or stock).",
                      "install_source")
        if c["install_source"] == "STOCK":
            field = ("installed_tire_id" if kind == "TIRE"
                     else "installed_battery_id")
            model = Tire if kind == "TIRE" else Battery
            item = (db.session.get(model, c[field]) if c[field] else None)
            if item is None or item.status != "IN_STOCK":
                raise err(f"Choose a {kind.lower()} that is in stock.", field)
            return
        # VENDOR: enough to register it.
        if not c["new_brand"]:
            raise err("Enter the brand of the new "
                      f"{kind.lower()}.", "new_brand")
        if kind == "TIRE" and not c["new_size"]:
            raise err("Enter the size of the new tire.", "new_size")
        if c["new_serial"]:
            model = Tire if kind == "TIRE" else Battery
            if model.query.filter_by(serial_number=c["new_serial"]).first():
                raise err(f"Serial {c['new_serial']} is already registered. "
                          "Pick it from stock instead, or check the "
                          "number.", "new_serial")

    # ── Completion ─────────────────────────────────────────────────────
    def unfinished(self, order):
        return [l for l in order.equipment_lines if l.status != "DONE"]

    def post(self, order, completed_date, user):
        """Turn every DONE, not-yet-posted line into transactions. Called
        by MaintenanceOrderService.complete() inside its transaction;
        does not commit."""
        from app.modules.transactions.tire_txn.service import (
            TireTransactionService)
        from app.modules.transactions.battery_txn.service import (
            BatteryTransactionService)
        tsvc, bsvc = TireTransactionService(), BatteryTransactionService()
        common = dict(transaction_date=completed_date, user=user,
                      vehicle_id=order.vehicle_id, source="MO",
                      maintenance_order_id=order.id,
                      odometer_at_service=order.odometer_at_service,
                      force_completed=True, commit=False)
        posted = 0
        for line in order.equipment_lines:
            if line.status != "DONE" or line.posted_at is not None:
                continue
            if line.kind == "TIRE":
                self._post_tire(order, line, tsvc, common, completed_date)
            else:
                self._post_battery(order, line, bsvc, common, completed_date)
            line.posted_at = datetime.utcnow()
            posted += 1
        db.session.flush()
        return posted

    @staticmethod
    def _auto_serial(order, line, suffix=""):
        pos = line.position or "BATT"
        return f"AUTO-{order.document_number or order.id}-{pos}{suffix}"[:60]

    def _current_tire(self, order, line):
        """The tire on that wheel now; registers a never-enrolled one."""
        if line.current_tire_id:
            return db.session.get(Tire, line.current_tire_id)
        desc = line.current_description or "Unknown"
        t = Tire(serial_number=self._auto_serial(order, line, "-OLD"),
                 brand=desc[:80], size="UNKNOWN", tire_type="RADIAL",
                 status="MOUNTED", vehicle_id=order.vehicle_id,
                 wheel_position=line.position,
                 branch_id=order.vehicle.branch_id if order.vehicle else None)
        db.session.add(t)
        db.session.flush()
        line.current_tire_id = t.id
        return t

    def _current_battery(self, order, line):
        if line.current_battery_id:
            return db.session.get(Battery, line.current_battery_id)
        desc = line.current_description or "Unknown"
        b = Battery(serial_number=self._auto_serial(order, line, "-OLD"),
                    brand=desc[:80], status="MOUNTED",
                    vehicle_id=order.vehicle_id,
                    branch_id=order.vehicle.branch_id
                    if order.vehicle else None)
        db.session.add(b)
        db.session.flush()
        line.current_battery_id = b.id
        return b

    def _installed_tire(self, order, line, completed_date):
        if line.install_source == "STOCK":
            return db.session.get(Tire, line.installed_tire_id)
        t = Tire(serial_number=line.new_serial or self._auto_serial(
                    order, line),
                 brand=line.new_brand, model=line.new_model,
                 size=line.new_size, tire_type="RADIAL",
                 vendor_id=order.vendor_id,
                 branch_id=order.vehicle.branch_id if order.vehicle else None,
                 purchase_date=completed_date,
                 tread_depth_initial=line.tread_depth_mm, status="IN_STOCK")
        db.session.add(t)
        db.session.flush()
        line.installed_tire_id = t.id
        return t

    def _installed_battery(self, order, line, completed_date):
        if line.install_source == "STOCK":
            return db.session.get(Battery, line.installed_battery_id)
        b = Battery(serial_number=line.new_serial or self._auto_serial(
                        order, line),
                    brand=line.new_brand, model=line.new_model,
                    warranty_months=line.new_warranty_months,
                    vendor_id=order.vendor_id,
                    branch_id=order.vehicle.branch_id
                    if order.vehicle else None,
                    purchase_date=completed_date, status="IN_STOCK")
        db.session.add(b)
        db.session.flush()
        line.installed_battery_id = b.id
        return b

    def _post_tire(self, order, line, svc, common, completed_date):
        current = self._current_tire(order, line)
        if line.action == "REPLACE":
            svc.create(tire_id=current.id, action="DISMOUNT",
                       wheel_position=line.position,
                       remarks=f"Removed on {order.document_number}",
                       **common)
            new = self._installed_tire(order, line, completed_date)
            svc.create(tire_id=new.id, action="MOUNT",
                       wheel_position=line.position,
                       tread_depth_mm=line.tread_depth_mm,
                       pressure_psi=line.pressure_psi,
                       remarks=line.remarks, **common)
        elif line.action == "ROTATE":
            svc.create(tire_id=current.id, action="ROTATE",
                       wheel_position=line.rotate_to_position,
                       tread_depth_mm=line.tread_depth_mm,
                       pressure_psi=line.pressure_psi,
                       remarks=(line.remarks or
                                f"{line.position} → {line.rotate_to_position}"),
                       **common)
        else:  # INSPECT | REPAIR
            svc.create(tire_id=current.id, action=line.action,
                       wheel_position=line.position,
                       tread_depth_mm=line.tread_depth_mm,
                       pressure_psi=line.pressure_psi,
                       remarks=line.remarks, **common)

    def _post_battery(self, order, line, svc, common, completed_date):
        current = self._current_battery(order, line)
        if line.action == "REPLACE":
            svc.create(battery_id=current.id, action="DISMOUNT",
                       remarks=f"Removed on {order.document_number}",
                       **common)
            new = self._installed_battery(order, line, completed_date)
            svc.create(battery_id=new.id, action="MOUNT",
                       voltage=line.voltage, test_result=line.test_result,
                       remarks=line.remarks, **common)
        else:  # TEST
            svc.create(battery_id=current.id, action="TEST",
                       voltage=line.voltage, test_result=line.test_result,
                       remarks=line.remarks, **common)

    # ── Serialising ────────────────────────────────────────────────────
    def line_json(self, l):
        def f(v):
            return float(v) if v is not None else None
        cur = l.current_tire if l.kind == "TIRE" else l.current_battery
        ins = l.installed_tire if l.kind == "TIRE" else l.installed_battery
        label = (self.tire_label if l.kind == "TIRE" else self.battery_label)
        return {
            "id": l.id, "kind": l.kind, "position": l.position,
            "action": l.action, "rotate_to_position": l.rotate_to_position,
            "current_tire_id": l.current_tire_id,
            "current_battery_id": l.current_battery_id,
            "current_description": l.current_description,
            "current_label": label(cur) if cur else None,
            "not_registered": cur is None,
            "install_source": l.install_source,
            "installed_tire_id": l.installed_tire_id,
            "installed_battery_id": l.installed_battery_id,
            "installed_label": label(ins) if ins else None,
            "new_serial": l.new_serial, "new_brand": l.new_brand,
            "new_model": l.new_model, "new_size": l.new_size,
            "new_warranty_months": l.new_warranty_months,
            "tread_depth_mm": f(l.tread_depth_mm),
            "pressure_psi": l.pressure_psi, "voltage": f(l.voltage),
            "test_result": l.test_result, "status": l.status,
            "posted": l.posted_at is not None, "remarks": l.remarks,
            "sort_order": l.sort_order,
        }
