import pytest


class _FakeMultiDict(dict):
    """Minimal stand-in for Flask's request.form (a werkzeug MultiDict)."""
    def get(self, key, default=None):
        return dict.get(self, key, default)


def test_flat_string_field_echoed_back():
    from app.core.validation.form_echo import FormEcho
    form = _FakeMultiDict({"brand": "Toyota", "model": "Hilux"})
    echo = FormEcho(form)
    assert echo.brand == "Toyota"
    assert echo.model == "Hilux"


def test_numeric_field_echoed_as_int_for_selected_comparisons():
    from app.core.validation.form_echo import FormEcho
    form = _FakeMultiDict({"vehicle_type_id": "3", "year": "2024"})
    echo = FormEcho(form)
    assert echo.vehicle_type_id == 3
    assert echo.year == 2024


def test_missing_field_returns_none():
    from app.core.validation.form_echo import FormEcho
    form = _FakeMultiDict({"brand": "Toyota"})
    echo = FormEcho(form)
    assert echo.nonexistent_field is None


def test_id_is_always_none_so_attachment_panels_stay_hidden():
    from app.core.validation.form_echo import FormEcho
    form = _FakeMultiDict({"id": "999", "brand": "Toyota"})
    echo = FormEcho(form)
    assert echo.id is None


def test_relation_override_takes_precedence():
    from app.core.validation.form_echo import FormEcho

    class _FakeBranch:
        id = 5
        name = "HQ"

    form = _FakeMultiDict({"branch_id": "5"})
    echo = FormEcho(form, branch=_FakeBranch())
    assert echo.branch.id == 5
    assert echo.branch.name == "HQ"


def test_relation_defaults_to_none_when_not_provided():
    from app.core.validation.form_echo import FormEcho
    form = _FakeMultiDict({})
    echo = FormEcho(form)
    assert echo.branch is None


def test_truthy_so_templates_treat_it_like_a_real_item():
    from app.core.validation.form_echo import FormEcho
    form = _FakeMultiDict({"brand": "Toyota"})
    echo = FormEcho(form)
    assert bool(echo) is True
