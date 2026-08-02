"""One-time backfill: normalize existing vehicles' fuel_type and transmission
to their canonical Lookup codes (v48).

The 156 vehicles imported before v48 hold the raw Excel text ('Gasoline',
'Manual'), while the edit form's <select> options carry the canonical Lookup
codes ('GASOLINE', 'MANUAL'). Because the comparison is exact, those dropdowns
render as unselected even though the data is present. This command rewrites the
stored value to the canonical code so existing records display correctly —
without re-importing anything.

Register in app/cli.py (or wherever CLI commands are wired):

    from app.modules.master_data.vehicle.backfill_cli import (
        register_vehicle_backfill_cli)
    register_vehicle_backfill_cli(app)

Usage:
    flask vehicle backfill-lookup-fields --dry-run    # preview, writes nothing
    flask vehicle backfill-lookup-fields              # apply

Idempotent: a value already equal to its canonical code is left untouched, so
re-running is a no-op. Safe to run repeatedly.
"""
import click
from flask.cli import AppGroup


def register_vehicle_backfill_cli(app):
    vehicle_cli = AppGroup("vehicle", help="Vehicle master maintenance tasks.")

    @vehicle_cli.command("backfill-lookup-fields")
    @click.option("--dry-run", is_flag=True,
                  help="Show what would change without writing.")
    def backfill_lookup_fields(dry_run):
        """Normalize fuel_type/transmission on existing vehicles to canonical
        Lookup codes so edit-form dropdowns show them as selected."""
        from app.extensions import db
        from app.modules.master_data.vehicle.models import Vehicle
        from app.modules.master_data.vehicle.import_coercion import (
            resolve_lookup, _load_lookup)

        # Load each lookup once (not per-row).
        fuel_map = _load_lookup("FUEL_TYPE")
        trans_map = _load_lookup("TRANSMISSION")
        if not fuel_map:
            click.echo("WARNING: FUEL_TYPE lookup is empty or missing — "
                       "fuel_type will be left unchanged.")
        if not trans_map:
            click.echo("WARNING: TRANSMISSION lookup is empty or missing — "
                       "transmission will be left unchanged.")

        changed = {"fuel_type": 0, "transmission": 0}
        examples = []
        for v in Vehicle.query.all():
            row_changes = []
            if v.fuel_type and fuel_map:
                canonical = resolve_lookup(v.fuel_type, "FUEL_TYPE",
                                           cache=fuel_map)
                if canonical != v.fuel_type:
                    row_changes.append(("fuel_type", v.fuel_type, canonical))
                    if not dry_run:
                        v.fuel_type = canonical
                    changed["fuel_type"] += 1
            if v.transmission and trans_map:
                canonical = resolve_lookup(v.transmission, "TRANSMISSION",
                                           cache=trans_map)
                if canonical != v.transmission:
                    row_changes.append(
                        ("transmission", v.transmission, canonical))
                    if not dry_run:
                        v.transmission = canonical
                    changed["transmission"] += 1
            if row_changes and len(examples) < 8:
                ident = v.plate_number or v.conduction_number or f"id={v.id}"
                examples.append((ident, row_changes))

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()

        click.echo("")
        click.echo(f"fuel_type    normalized: {changed['fuel_type']}")
        click.echo(f"transmission normalized: {changed['transmission']}")
        if examples:
            click.echo("\nExamples:")
            for ident, ch in examples:
                for field, old, new in ch:
                    click.echo(f"  {ident:12s} {field}: {old!r} -> {new!r}")
        click.echo("\n(dry run — nothing written)" if dry_run
                   else "\nDone. Reload a vehicle's edit page to confirm the "
                        "dropdowns now show the value as selected.")

    app.cli.add_command(vehicle_cli)
