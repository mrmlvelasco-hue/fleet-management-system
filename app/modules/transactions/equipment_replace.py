"""Shared rules for a standalone tire / battery REPLACE document
(2026-10-19, mockup "New tire transaction").

One document carries both sides of the swap: the item that came off
(removed_*), where it went (removed_to) and why, and the item that went
on -- picked from stock, or supplied new by the workshop and registered
from the new_* fields when the document completes. Workshop, technician,
cost, scope of work and the optional open MO it belongs to travel with it.
"""
from app.extensions import db

REMOVED_TO = ("FOR_DISPOSAL", "REPAIRABLE")
INSTALL_SOURCES = ("VENDOR", "STOCK")
CLOSED_MO_STATUSES = ("COMPLETED", "CANCELLED", "REJECTED")


def clean(v, n=None):
    v = (str(v).strip() if v is not None else "") or None
    return v[:n] if (v and n) else v


def check_linked_mo(mo_id, vehicle_id, err):
    """An optional link to an OPEN Maintenance Order on the same vehicle
    (its cost is then the MO's, not counted again)."""
    if not mo_id:
        return
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    mo = db.session.get(MaintenanceOrder, int(mo_id))
    if mo is None or mo.vehicle_id != vehicle_id:
        raise err("Link an order on this same vehicle.",
                  "maintenance_order_id")
    if (mo.status or "").upper() in CLOSED_MO_STATUSES:
        raise err(f"{mo.document_number or 'That order'} is closed; link an "
                  "open order or leave it unlinked.", "maintenance_order_id")


def check_replace(*, kind, vehicle_id, wheel_position, install_source,
                  item_id, new_serial, new_brand, new_size, removed_to,
                  model, err):
    """Validate a REPLACE before anything is written. `model` is the
    master (Tire / Battery); `item_id` the stock item for STOCK."""
    noun = "tire" if kind == "TIRE" else "battery"
    if not vehicle_id:
        raise err(f"Choose the vehicle the {noun} is on.", "vehicle_id")
    if kind == "TIRE" and not wheel_position:
        raise err("Choose the wheel position.", "wheel_position")
    if removed_to and removed_to not in REMOVED_TO:
        raise err("Send the removed one to stock for disposal, or to stock "
                  "as repairable.", "removed_to")
    if install_source not in INSTALL_SOURCES:
        raise err(f"Say where the new {noun} came from (workshop or "
                  "stock).", "install_source")
    if install_source == "STOCK":
        item = db.session.get(model, item_id) if item_id else None
        if item is None or item.status != "IN_STOCK":
            raise err(f"Choose a {noun} that is in stock.",
                      "tire_id" if kind == "TIRE" else "battery_id")
        return
    if not new_brand:
        raise err(f"Enter the brand of the new {noun}.", "new_brand")
    if kind == "TIRE" and not new_size:
        raise err("Enter the size of the new tire.", "new_size")
    if new_serial and model.query.filter_by(serial_number=new_serial).first():
        raise err(f"Serial {new_serial} is already registered. Pick it from "
                  "stock instead, or check the number.", "new_serial")


def auto_serial(prefix, txn):
    return f"AUTO-{txn.document_number or f'{prefix}{txn.id}'}"[:60]


def scope_rows(items):
    """['text', ...] or [{'activity_description', 'is_done'}] -> rows."""
    out = []
    for i, it in enumerate(items or []):
        if isinstance(it, dict):
            text = clean(it.get("activity_description"), 255)
            done = it.get("is_done", True) not in (False, "false", 0, "0")
        else:
            text, done = clean(it, 255), True
        if text:
            out.append({"activity_description": text, "is_done": bool(done),
                        "sort_order": i})
    return out
