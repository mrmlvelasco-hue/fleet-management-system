"""Vendor Information Summary -- one builder for every screen that shows it.

Used by GET /reference/vendors/<id>/summary (the New MO form, as soon as
a vendor is picked) and embedded in the MO detail payload (what the
approvers see). One function so the two can never disagree about what a
vendor "is" -- the same reason reference_resolver centralises labels.

Read-only and deliberately NOT behind vendor.view: it is the identity
and contact card of a supplier already attached to a document the
caller can see, not the vendor master. Agreed with the client (B23):
anyone on the MO may see the contact details.
"""


def vendor_serves_branch(vendor, branch_id):
    """True / False / None (= cannot tell).

    Mirrors VendorService._is_visible's convention: a vendor with no
    branch AND no business-unit assignment serves everyone. A vendor
    scoped only by business unit cannot be checked against a branch
    (Branch carries no business-unit link), so the answer is None and
    nothing is flagged -- a false warning would train people to ignore
    the real ones.
    """
    if branch_id is None:
        return None
    branches = list(vendor.branches or [])
    units = list(vendor.business_units or [])
    if not branches and not units:
        return True
    if any(b.id == branch_id for b in branches):
        return True
    if not branches:
        return None
    return False


def vendor_summary(vendor, vehicle=None):
    if vendor is None:
        return None
    branches = sorted(vendor.branches or [], key=lambda b: b.name or "")
    vehicle_branch = getattr(vehicle, "branch", None) if vehicle else None
    return {
        "id": vendor.id,
        "code": vendor.code,
        "name": vendor.name,
        "label": f"{vendor.code} — {vendor.name}",
        "vendor_type": vendor.vendor_type,
        "tin": vendor.tin,
        "contact_person": vendor.contact_person,
        "phone": vendor.phone,
        "email": vendor.email,
        "address": vendor.address,
        "city": vendor.city,
        "is_active": bool(vendor.is_active),
        "branches": [{"id": b.id, "name": b.name} for b in branches],
        "serves_all_branches": not branches and not (vendor.business_units or []),
        "contacts": [
            {"name": c.contact_name, "position": c.position,
             "tel": c.tel_number, "cel": c.cel_number, "email": c.email}
            for c in sorted(getattr(vendor, "other_contacts", None) or [],
                            key=lambda c: c.id)],
        "vehicle_branch": vehicle_branch.name if vehicle_branch else None,
        "serves_vehicle_branch": vendor_serves_branch(
            vendor, vehicle_branch.id if vehicle_branch else None),
    }
