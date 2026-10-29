"""Stage 3D: propagate Company onto every branch-scoped record.

The migration/transaction script for the Branch -> Company replacement.
Once each Branch carries its Company (set in Master Data -> Branches, or
by the Stage 3B mapping-sheet importer), this walks every branch-scoped
master and transaction and fills in `company_id` from that mapping, so
the data stands on its own once Branch is retired.

Operating rules, each one deliberate:

* **Dry run by default.** `run()` previews; `run(dry_run=False)` writes.
  Same convention as the fuel importer and the Stage 3B migration tool,
  so nobody changes production by forgetting a flag.
* **Idempotent.** Only rows whose `company_id` IS NULL are touched, so
  re-running is a no-op and a half-finished run can simply be re-run.
* **Never overwrites.** A record already carrying a Company -- a vehicle
  deliberately assigned one other than its branch's, which Stage 3A
  allows -- is left exactly as it is and counted under `already_set`.
* **Never guesses.** A record under a branch with no Company mapped is
  left NULL and its branch is listed in `unmapped_branches`, which is
  the list of work remaining for whoever is doing the mapping.
* **Transactions follow their vehicle.** A document with no branch of its
  own belongs to the org context of the vehicle it was raised against.

The report is the same shape on dry run and apply, so a preview can be
compared directly against the result.
"""
from app.extensions import db


class CompanyBackfillService:
    """Fill `company_id` across the company-axis tables (Stage 3D)."""

    def run(self, dry_run: bool = True) -> dict:
        from app.modules.master_data.org.models import Branch, OrgCompany

        # branch_id -> company_id, the single source this whole backfill
        # derives from.
        branch_company = {
            b.id: b.company_id
            for b in Branch.query.all()
        }
        mapped = {bid: cid for bid, cid in branch_company.items() if cid}

        report = {
            "dry_run": dry_run,
            "tables": {},
            "unmapped_branches": [
                {"id": b.id, "code": b.code, "name": b.name}
                for b in Branch.query.filter(
                    Branch.company_id.is_(None)).order_by(Branch.code).all()
            ],
            "companies_available": OrgCompany.query.count(),
        }

        for name, handler in self._handlers().items():
            report["tables"][name] = handler(mapped, dry_run)

        if not dry_run:
            db.session.commit()
        else:
            # A dry run must leave the session exactly as it found it --
            # the counting below never writes, but rolling back makes
            # that a guarantee rather than a promise.
            db.session.rollback()
        return report

    # ── per-table handlers ──────────────────────────────────────────

    def _handlers(self):
        return {
            "vehicles": self._by_branch_column(
                "app.modules.master_data.vehicle.models", "Vehicle"),
            "drivers": self._by_branch_column(
                "app.modules.master_data.driver.models", "Driver"),
            "departments": self._by_branch_column(
                "app.modules.master_data.org.models", "Department"),
            "tires": self._by_branch_column(
                "app.modules.master_data.tire.models", "Tire"),
            "batteries": self._by_branch_column(
                "app.modules.master_data.battery.models", "Battery"),
            "users": self._by_branch_column(
                "app.modules.user_management.models", "User"),
            "approval_tasks": self._by_branch_column(
                "app.core.approval.models", "ApprovalTask"),
            "approval_instances": self._by_branch_column(
                "app.core.approval.models", "ApprovalInstance"),
            "vehicle_handover_checklists": self._by_branch_then_vehicle(
                "app.modules.transactions.vehicle_handover.models",
                "VehicleHandoverChecklist"),
            "vehicle_checklists": self._by_branch_then_vehicle(
                "app.modules.transactions.vehicle_checklist.models",
                "VehicleChecklist"),
            "maintenance_orders": self._maintenance_orders,
            "vendor_companies": self._vendor_companies,
        }

    @staticmethod
    def _model(path, name):
        import importlib
        return getattr(importlib.import_module(path), name)

    def _by_branch_column(self, path, name,
                          branch_attr="branch_id", company_attr="company_id"):
        """Records that carry their own branch_id: Company comes straight
        from that branch's mapping."""
        def handler(mapped, dry_run):
            model = self._model(path, name)
            stats = {"would_update": 0, "updated": 0, "already_set": 0,
                     "unresolved": 0}
            for row in model.query.all():
                if getattr(row, company_attr) is not None:
                    stats["already_set"] += 1
                    continue
                company_id = mapped.get(getattr(row, branch_attr))
                if not company_id:
                    stats["unresolved"] += 1
                    continue
                if dry_run:
                    stats["would_update"] += 1
                else:
                    setattr(row, company_attr, company_id)
                    stats["updated"] += 1
            return stats
        return handler

    def _by_branch_then_vehicle(self, path, name):
        """Transaction documents: their own branch if they have one, else
        the Company of the vehicle they were raised against."""
        def handler(mapped, dry_run):
            model = self._model(path, name)
            stats = {"would_update": 0, "updated": 0, "already_set": 0,
                     "unresolved": 0, "via_vehicle": 0}
            for row in model.query.all():
                if row.company_id is not None:
                    stats["already_set"] += 1
                    continue
                company_id = mapped.get(getattr(row, "branch_id", None))
                if not company_id:
                    vehicle = getattr(row, "vehicle", None)
                    if vehicle is not None:
                        company_id = (vehicle.company_id
                                      or mapped.get(vehicle.branch_id))
                        if company_id:
                            stats["via_vehicle"] += 1
                if not company_id:
                    stats["unresolved"] += 1
                    continue
                if dry_run:
                    stats["would_update"] += 1
                else:
                    row.company_id = company_id
                    stats["updated"] += 1
            return stats
        return handler

    def _maintenance_orders(self, mapped, dry_run):
        """Both ends of a transfer, independently: a move has a From and
        a To, and one being unmappable must not discard the other."""
        model = self._model(
            "app.modules.transactions.maintenance_order.models",
            "MaintenanceOrder")
        stats = {"would_update": 0, "updated": 0, "already_set": 0,
                 "unresolved": 0}
        pairs = (("origin_branch_id", "origin_company_id"),
                 ("destination_branch_id", "destination_company_id"))
        for row in model.query.all():
            touched = False
            for branch_attr, company_attr in pairs:
                if getattr(row, company_attr) is not None:
                    stats["already_set"] += 1
                    continue
                branch_id = getattr(row, branch_attr, None)
                if branch_id is None:
                    # Not a transfer: this end is legitimately empty, so
                    # it is neither work done nor work outstanding.
                    continue
                company_id = mapped.get(branch_id)
                if not company_id:
                    stats["unresolved"] += 1
                    continue
                if dry_run:
                    stats["would_update"] += 1
                else:
                    setattr(row, company_attr, company_id)
                    touched = True
            if touched:
                stats["updated"] += 1
        return stats

    def _vendor_companies(self, mapped, dry_run):
        """Vendor coverage is many-to-many: each branch a vendor serves
        becomes that branch's Company, de-duplicated because several
        branches can roll up to one Company."""
        from app.modules.master_data.vendor.models import (
            Vendor, vendor_companies)
        stats = {"would_update": 0, "updated": 0, "already_set": 0,
                 "unresolved": 0}
        existing = {
            (r.vendor_id, r.company_id)
            for r in db.session.execute(vendor_companies.select()).all()
        }
        for vendor in Vendor.query.all():
            wanted = set()
            for branch in getattr(vendor, "branches", []) or []:
                company_id = mapped.get(branch.id)
                if company_id:
                    wanted.add(company_id)
                else:
                    stats["unresolved"] += 1
            for company_id in wanted:
                if (vendor.id, company_id) in existing:
                    stats["already_set"] += 1
                    continue
                if dry_run:
                    stats["would_update"] += 1
                else:
                    db.session.execute(vendor_companies.insert().values(
                        vendor_id=vendor.id, company_id=company_id))
                    existing.add((vendor.id, company_id))
                    stats["updated"] += 1
        return stats


class CompanyReconciliationService:
    """Prove the Company axis agrees with the Branch axis (Stage 3D).

    The checkpoint between backfilling Company data and letting any code
    read it. Read-only by construction: it never writes, so it cannot
    disturb what it is measuring.

    Per table it reports:
      * ``total``            -- rows considered;
      * ``missing_company``  -- rows with no Company yet (outstanding
        work: either an unmapped branch, or a backfill not yet applied);
      * ``mismatched``       -- rows whose Company is NOT the one their
        branch maps to. Legitimate for a deliberate Stage 3A override,
        which is why these are surfaced for a human rather than
        "corrected" silently;
      * ``agrees``           -- rows whose Company matches their branch.

    ``balanced`` is True only when nothing is missing and nothing
    mismatches -- i.e. when it is safe to let code read Company.
    """

    def run(self) -> dict:
        from app.modules.master_data.org.models import Branch

        mapped = {b.id: b.company_id
                  for b in Branch.query.all() if b.company_id}

        svc = CompanyBackfillService()
        tables = {}
        for name, spec in self._specs().items():
            tables[name] = self._check(svc, mapped, *spec)

        unmapped = [
            {"id": b.id, "code": b.code, "name": b.name}
            for b in Branch.query.filter(
                Branch.company_id.is_(None)).order_by(Branch.code).all()
        ]
        balanced = all(t["missing_company"] == 0 and t["mismatched"] == 0
                       for t in tables.values())
        return {"tables": tables, "unmapped_branches": unmapped,
                "balanced": balanced}

    @staticmethod
    def _specs():
        return {
            "vehicles": ("app.modules.master_data.vehicle.models", "Vehicle"),
            "drivers": ("app.modules.master_data.driver.models", "Driver"),
            "departments": ("app.modules.master_data.org.models",
                            "Department"),
            "tires": ("app.modules.master_data.tire.models", "Tire"),
            "batteries": ("app.modules.master_data.battery.models",
                          "Battery"),
            "users": ("app.modules.user_management.models", "User"),
            "approval_tasks": ("app.core.approval.models", "ApprovalTask"),
            "approval_instances": ("app.core.approval.models",
                                   "ApprovalInstance"),
            "vehicle_handover_checklists": (
                "app.modules.transactions.vehicle_handover.models",
                "VehicleHandoverChecklist"),
            "vehicle_checklists": (
                "app.modules.transactions.vehicle_checklist.models",
                "VehicleChecklist"),
            "maintenance_orders": (
                "app.modules.transactions.maintenance_order.models",
                "MaintenanceOrder"),
        }

    @staticmethod
    def _check(svc, mapped, path, name):
        model = svc._model(path, name)
        stats = {"total": 0, "missing_company": 0, "mismatched": 0,
                 "agrees": 0, "no_branch": 0}
        # MaintenanceOrder's Company lives on the transfer ends, so its
        # origin pair is what reconciles against origin_branch_id.
        if name == "MaintenanceOrder":
            pairs = [("origin_branch_id", "origin_company_id"),
                     ("destination_branch_id", "destination_company_id")]
        else:
            pairs = [("branch_id", "company_id")]

        for row in model.query.all():
            for branch_attr, company_attr in pairs:
                branch_id = getattr(row, branch_attr, None)
                company_id = getattr(row, company_attr, None)
                if branch_id is None and company_id is None:
                    # Legitimately empty on both axes (a non-transfer MO
                    # end, a GLOBAL user): neither work nor a problem.
                    stats["no_branch"] += 1
                    continue
                stats["total"] += 1
                expected = mapped.get(branch_id)
                if company_id is None:
                    stats["missing_company"] += 1
                elif expected is not None and company_id != expected:
                    stats["mismatched"] += 1
                else:
                    stats["agrees"] += 1
        return stats
