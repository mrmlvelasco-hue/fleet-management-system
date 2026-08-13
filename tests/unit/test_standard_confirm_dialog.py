"""One standard confirmation dialog across the application.

Reported from a screenshot: removing a part showed a raw browser
confirm() -- an unstyled OS dialog displaying the site's IP address,
sitting next to an otherwise fully themed UI. Three different
confirmation styles were in use across the app (SweetAlert2, raw
window.confirm, and inline onclick="return confirm(...)").
"""
import glob


def _app_js():
    return open("app/static/js/app.js").read()


def _all_templates():
    return [p for p in glob.glob("app/**/templates/**/*.html", recursive=True)]


def test_no_raw_browser_confirm_remains_in_any_template():
    """The actual regression. A raw confirm() cannot be themed and
    renders as an OS dialog showing the server address."""
    offenders = []
    for path in _all_templates():
        text = open(path, encoding="utf-8").read()
        if ("window.confirm(" in text
                or 'onclick="return confirm(' in text
                or 'onsubmit="return confirm(' in text):
            offenders.append(path)
    assert not offenders, f"raw browser dialogs still present in: {offenders}"


def test_standard_helper_exists():
    assert "window.fmsConfirm" in _app_js()


def test_helper_falls_back_when_sweetalert_is_unavailable():
    """If SweetAlert2 fails to load, the question must still be asked.
    Silently proceeding with a delete because a stylesheet didn't load
    would be far worse than an ugly dialog."""
    js = _app_js()
    start = js.index("window.fmsConfirm = function")
    body = js[start:start + 900]
    assert "if (!window.Swal)" in body
    assert "window.confirm(message)" in body


def test_destructive_actions_are_visually_distinct():
    """"Remove this part?" and "Submit for approval?" must not look
    identical at the moment someone clicks without fully reading."""
    js = _app_js()
    assert "DESTRUCTIVE" in js
    assert "#dc3545" in js          # red confirm button
    assert "focusCancel: danger" in js   # Enter won't confirm a delete


def test_cancel_is_placed_away_from_the_destructive_button():
    assert "reverseButtons: true" in _app_js()


def test_declarative_data_confirm_is_supported_on_forms_links_and_buttons():
    """Buttons need their own support separately from forms: one form
    can carry several submit buttons with different formaction targets
    (Save / Test / Run Backup) where only one warrants a confirmation."""
    js = _app_js()
    assert 'form.matches("[data-confirm]")' in js
    assert 'a[data-confirm]' in js
    assert 'button[data-confirm]' in js


def test_backup_run_uses_the_standard_confirm():
    text = open("app/modules/system_admin/templates/system_admin/"
                "backup_config.html", encoding="utf-8").read()
    assert "data-confirm=" in text
    assert "return confirm(" not in text


def test_scheduled_report_delete_uses_the_standard_confirm():
    text = open("app/modules/system_admin/templates/system_admin/"
                "scheduled_report_list.html", encoding="utf-8").read()
    assert "data-confirm=" in text
    assert "return confirm(" not in text


def test_maintenance_order_and_invoice_line_deletes_use_the_helper():
    for path in ("app/modules/transactions/templates/transactions/"
                 "maintenanceorder_detail.html",
                 "app/modules/transactions/templates/transactions/"
                 "maintenanceinvoice_detail.html"):
        text = open(path, encoding="utf-8").read()
        assert "window.fmsConfirm(" in text, f"{path} not using the helper"
        assert "window.confirm(" not in text, f"{path} still has a raw dialog"
