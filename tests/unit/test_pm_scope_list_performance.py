"""PM Scope Template list performance.

Reported as laggy. Measured on a real 4,626-template / 109,879-item
VEMS import before any change: 5.59s server-side and 7.1 MB of HTML,
because the index eagerly loaded every scope item object just to render
eight activity-code badges per row.

After switching the index to a GROUP BY count: 1.32s and 5.3 MB.
"""
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


def _client(app, db):
    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


@pytest.fixture()
def template_with_items(app, db):
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.maintenance_config.service import PMScopeTemplateService
    mt = MaintenanceTypeService().create(code="PM-PERF", name="PM Perf", category="PREVENTIVE")
    tmpl = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Perf Template",
        items=[{"activity_code": f"A-{i:03d}",
                "activity_description": f"Activity {i}",
                "sort_order": i} for i in range(23)])
    return tmpl


def test_list_with_counts_returns_the_right_activity_count(
        app, db, template_with_items):
    from app.modules.maintenance_config.service import PMScopeTemplateService
    rows = PMScopeTemplateService().list_with_counts(include_inactive=True)
    match = [(t, n) for t, n in rows if t.id == template_with_items.id]
    assert len(match) == 1
    assert match[0][1] == 23


def test_list_with_counts_does_not_load_scope_item_objects(
        app, db, template_with_items):
    """The actual fix: the index must answer 'how many activities'
    without materialising a single PMScopeItem. Confirmed by checking
    the relationship was never loaded onto the returned instance."""
    from sqlalchemy import inspect as sa_inspect
    from app.extensions import db as _db
    from app.modules.maintenance_config.service import PMScopeTemplateService

    _db.session.expunge_all()
    rows = PMScopeTemplateService().list_with_counts(include_inactive=True)
    template = next(t for t, _n in rows if t.id == template_with_items.id)
    unloaded = sa_inspect(template).unloaded
    assert "items" in unloaded, (
        "scope items were eagerly loaded -- this is the exact cost the "
        "index page was changed to avoid")


def test_a_template_whose_items_were_removed_reports_zero(app, db):
    """A template can't be CREATED empty (the service correctly refuses
    that), but its items can be removed later. The count query must
    report 0 for it rather than dropping the template off the index
    entirely -- an empty template is exactly the kind of thing an admin
    needs to see in order to fix it."""
    from app.extensions import db as _db
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.maintenance_config.service import PMScopeTemplateService
    from app.modules.maintenance_config.models import PMScopeItem

    mt = MaintenanceTypeService().create(code="PM-EMPTY", name="PM Empty",
                                         category="PREVENTIVE")
    tmpl = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Empty Template",
        items=[{"activity_code": "A-001",
                "activity_description": "Will be removed",
                "sort_order": 1}])
    PMScopeItem.query.filter_by(template_id=tmpl.id).delete()
    _db.session.commit()

    rows = PMScopeTemplateService().list_with_counts(include_inactive=True)
    match = [(t, n) for t, n in rows if t.id == tmpl.id]
    assert len(match) == 1, "template disappeared from the index"
    assert match[0][1] == 0


def test_index_page_renders_the_activity_count(
        app, db, template_with_items):
    html = _client(app, db).get(
        "/admin/pm-scope-templates").get_data(as_text=True)
    assert "23 activities" in html


def test_singular_wording_for_a_single_activity(app, db):
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.maintenance_config.service import PMScopeTemplateService
    mt = MaintenanceTypeService().create(code="PM-ONE", name="PM One", category="PREVENTIVE")
    PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Single Template",
        items=[{"activity_code": "A-001",
                "activity_description": "Only one", "sort_order": 1}])
    html = _client(app, db).get(
        "/admin/pm-scope-templates").get_data(as_text=True)
    assert "1 activity<" in html or "1 activity " in html


def test_the_original_list_method_is_unchanged(app, db, template_with_items):
    """list() still eager-loads items for callers that genuinely need
    the objects -- only the index page switched to counts."""
    from app.modules.maintenance_config.service import PMScopeTemplateService
    items = PMScopeTemplateService().list(include_inactive=True)
    template = next(t for t in items if t.id == template_with_items.id)
    assert len(template.items) == 23


def test_detail_page_still_shows_every_activity(
        app, db, template_with_items):
    """The activity codes moved off the index, so the detail page must
    still show the real list -- that's where they went."""
    html = _client(app, db).get(
        f"/admin/pm-scope-templates/{template_with_items.id}"
    ).get_data(as_text=True)
    assert "A-000" in html
    assert "A-022" in html
