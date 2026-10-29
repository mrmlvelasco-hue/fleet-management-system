"""Stage 3B (D6): Branch -> Company migration tool.

Backfills `vehicles.company_id` from `branch_id` against a client-
approved Branch/Company mapping sheet -- one row per Branch naming the
Company (short_name + Region + Area/District, per D2's uniqueness key)
that Branch maps to. Dry-run review report first, apply second; never
guesses on ambiguity, and never overwrites a Vehicle.company_id an
admin already set manually (Stage 3A already allows that).

Reads the sheet by column HEADER through an alias table, the same
provider-agnostic approach already proven for fuel statement imports
(`app/modules/transactions/fuel/import_export.py`) -- the real sheet's
exact header spellings aren't confirmed yet, so this needs no code
change once they are, only (if needed) a new alias entry.

A Company named in the sheet is created if it doesn't already exist for
that exact (short_name, region, area) triple, or reused if it does --
so re-running the same file, or a corrected re-upload, is always safe:
nothing is ever duplicated. When a NEW Company is created, its
`legacy_branch_id` is set to that row's Branch (`OrgCompany`'s own
documented convention) -- only from the FIRST row that creates it, if
several branches turn out to consolidate under the same Company.

**Branch.company_id (Stage 3B follow-up):** every row's Branch also
gets its `company_id` backfilled to the resolved Company -- the EDL
Group hierarchy's real storage (one Company has several Branches: San
Mateo, Cavite 2, South Luzon, ...), so any branch-scoped master data
(Driver/Assignee, Department, ...) can read Company straight off its
own branch_id with no column of its own. Same never-overwrite rule as
Vehicle: a Branch whose company_id is already set (manually, or by an
earlier run) is left alone and counted separately, never clobbered by
a later or corrected sheet.

**Recommended follow-up (not done here):** `_load_rows`/`_norm`/
`_map_headers` below intentionally duplicate the equivalent helpers in
`fuel/import_export.py` rather than importing them, because those are
named as private helpers of that module and refactoring either module
to share them was judged out of scope for this tool. Extracting a
single shared `app/core/import_utils.py` the next time either import
format needs a real change would remove the duplication cleanly.
"""
import re as _re
from io import BytesIO

from openpyxl import load_workbook

from app.extensions import db
from app.modules.master_data.org.models import Branch, Region, AreaDistrict, OrgCompany
from app.modules.master_data.vehicle.models import Vehicle

COLUMN_ALIASES = {
    "branch_code": ("branch_code", "branch", "branch code"),
    "company_short_name": ("company_short_name", "short_name", "company",
                           "company short name", "company name short"),
    "company_name": ("company_name", "company full name", "full name"),
    "region_code": ("region_code", "region"),
    "area_district_code": ("area_district_code", "area_district", "area",
                           "district", "area/district", "area district"),
}

REQUIRED_FIELDS = ("branch_code", "company_short_name", "region_code",
                   "area_district_code")


def _norm(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = _re.sub(r"[^a-z0-9]+", " ", text)
    return _re.sub(r"\s+", " ", text).strip()


def _load_rows(file_stream):
    raw = file_stream.read()
    if not raw or not raw.strip():
        raise ValueError("The file is empty.")
    try:
        wb = load_workbook(BytesIO(raw), data_only=True)
    except Exception as exc:
        raise ValueError(
            f"Could not read this file as an Excel workbook: {exc}")
    ws = wb.active
    all_rows = [[cell.value for cell in row] for row in ws.iter_rows()]
    if not all_rows:
        raise ValueError("The file has no rows.")
    return all_rows[0], all_rows[1:]


def _map_headers(header_row):
    normalized_aliases = {
        field: {_norm(a) for a in aliases}
        for field, aliases in COLUMN_ALIASES.items()
    }
    mapping = {}
    for index, raw in enumerate(header_row):
        text = _norm(raw)
        if not text:
            continue
        for field, aliases in normalized_aliases.items():
            if field in mapping:
                continue
            if text in aliases:
                mapping[field] = index
                break
    return mapping


def _cell(row, mapping, field):
    idx = mapping.get(field)
    if idx is None or idx >= len(row):
        return None
    value = row[idx]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def import_company_mapping(file_stream, dry_run: bool = True) -> dict:
    """Preview (dry_run=True, default) or apply (dry_run=False) a
    Branch -> Company mapping sheet. Returns a report dict with the
    same shape either way, so a preview and its eventual apply are
    directly comparable."""
    header, data_rows = _load_rows(file_stream)
    mapping = _map_headers(header)

    missing = [f for f in REQUIRED_FIELDS if f not in mapping]
    if missing:
        raise ValueError(
            "Could not find these column(s) in the file: "
            + ", ".join(missing)
            + ". Columns are matched by name — check the header row.")

    stats = {
        "total_rows": 0,
        "dry_run": dry_run,
        "applied": False,
        "companies_created": [],
        "companies_matched_existing": [],
        "vehicles_updated": 0,
        "vehicles_already_assigned_skipped": 0,
        "branches_updated": 0,
        "branches_already_assigned_skipped": 0,
        "errors": [],
    }

    # Pre-pass: a Branch named more than once in this file, mapped to
    # more than one distinct (short_name, region, area) triple, is
    # ambiguous — skipped entirely for every row naming it, never
    # guessed at.
    branch_triples = {}
    for row in data_rows:
        if all(c is None for c in row):
            continue
        bcode = _cell(row, mapping, "branch_code")
        if not bcode:
            continue
        triple = (_cell(row, mapping, "company_short_name"),
                 _cell(row, mapping, "region_code"),
                 _cell(row, mapping, "area_district_code"))
        branch_triples.setdefault(bcode, set()).add(triple)
    ambiguous_branches = {b for b, triples in branch_triples.items()
                          if len(triples) > 1}

    created_this_run = {}  # (short_name, region_id, area_id) -> OrgCompany

    for i, row in enumerate(data_rows, start=2):
        if all(c is None for c in row):
            continue
        stats["total_rows"] += 1

        branch_code = _cell(row, mapping, "branch_code")
        short_name = _cell(row, mapping, "company_short_name")
        company_name = _cell(row, mapping, "company_name")
        region_code = _cell(row, mapping, "region_code")
        area_code = _cell(row, mapping, "area_district_code")

        problems = []
        if not branch_code:
            problems.append("branch_code is required")
        if not short_name:
            problems.append("company_short_name is required")
        if not region_code:
            problems.append("region_code is required")
        if not area_code:
            problems.append("area_district_code is required")
        if problems:
            stats["errors"].append({
                "row": i, "branch_code": branch_code,
                "problems": problems, "type": "invalid_row"})
            continue

        if branch_code in ambiguous_branches:
            stats["errors"].append({
                "row": i, "branch_code": branch_code,
                "problems": [
                    f"Branch '{branch_code}' maps to more than one company "
                    "in this file — skipped, nothing changed for this "
                    "branch."],
                "type": "ambiguous_branch"})
            continue

        branch = Branch.query.filter_by(code=branch_code).first()
        if branch is None:
            stats["errors"].append({
                "row": i, "branch_code": branch_code,
                "problems": [f"No Branch with code '{branch_code}'."],
                "type": "unresolved_branch"})
            continue

        region = Region.query.filter_by(code=region_code).first()
        if region is None:
            stats["errors"].append({
                "row": i, "branch_code": branch_code,
                "problems": [f"No Region with code '{region_code}'."],
                "type": "unresolved_region"})
            continue

        area = AreaDistrict.query.filter_by(
            code=area_code, region_id=region.id).first()
        if area is None:
            stats["errors"].append({
                "row": i, "branch_code": branch_code,
                "problems": [
                    f"No Area/District '{area_code}' under Region "
                    f"'{region_code}'."],
                "type": "unresolved_area_district"})
            continue

        triple_key = (short_name, region.id, area.id)
        company = created_this_run.get(triple_key)
        if company is None:
            company = OrgCompany.query.filter_by(
                short_name=short_name, region_id=region.id,
                area_district_id=area.id).first()

        if company is None:
            stats["companies_created"].append({
                "row": i, "branch_code": branch_code,
                "short_name": short_name, "region_code": region_code,
                "area_district_code": area_code})
            if not dry_run:
                company = OrgCompany(
                    short_name=short_name, company_name=company_name,
                    region_id=region.id, area_district_id=area.id,
                    legacy_branch_id=branch.id)
                db.session.add(company)
                db.session.flush()
                created_this_run[triple_key] = company
        else:
            stats["companies_matched_existing"].append({
                "row": i, "branch_code": branch_code,
                "short_name": short_name, "region_code": region_code,
                "area_district_code": area_code,
                "company_id": company.id})
            created_this_run[triple_key] = company

        if dry_run:
            # Preview the Branch/vehicle impact even for a company that
            # would only be created on apply — whether branch.company_id
            # is already set is already known regardless of `company`.
            if branch.company_id is not None:
                stats["branches_already_assigned_skipped"] += 1
            else:
                stats["branches_updated"] += 1
            for v in Vehicle.query.filter_by(branch_id=branch.id).all():
                if v.company_id is not None:
                    stats["vehicles_already_assigned_skipped"] += 1
                else:
                    stats["vehicles_updated"] += 1
            continue

        if branch.company_id is not None:
            stats["branches_already_assigned_skipped"] += 1
        else:
            branch.company_id = company.id
            stats["branches_updated"] += 1

        for v in Vehicle.query.filter_by(branch_id=branch.id).all():
            if v.company_id is not None:
                stats["vehicles_already_assigned_skipped"] += 1
            else:
                v.company_id = company.id
                stats["vehicles_updated"] += 1

    if not dry_run:
        db.session.commit()
        stats["applied"] = True
    return stats
