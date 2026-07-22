"""Tests for the `peso` Jinja currency filter -- consistent comma-
separated, 2-decimal money formatting across every UI and print
template (the reported inconsistency: some places had thousands
separators, others didn't).
"""
from decimal import Decimal

import pytest


@pytest.fixture()
def render(app):
    from flask import render_template_string
    def _render(template):
        with app.test_request_context():
            return render_template_string(template)
    return _render


def test_formats_with_thousands_separators_and_two_decimals(render):
    assert render("{{ 950000|peso }}") == "₱950,000.00"


def test_formats_a_decimal_value(render):
    assert render("{{ 5500.5|peso }}") == "₱5,500.50"


def test_formats_a_large_value(render):
    assert render("{{ 1234567.891|peso }}") == "₱1,234,567.89"


def test_none_renders_as_em_dash_not_the_word_none(render):
    assert render("{{ None|peso }}") == "—"


def test_blank_string_renders_as_em_dash(render):
    assert render("{{ ''|peso }}") == "—"


def test_symbol_can_be_suppressed_for_table_columns(render):
    # Report tables often put the ₱ in the header, not every cell.
    assert render("{{ 950000|peso(symbol=False) }}") == "950,000.00"


def test_accepts_a_numeric_string(render):
    assert render("{{ '2500'|peso }}") == "₱2,500.00"


def test_non_numeric_value_is_returned_unchanged_not_crashing(render):
    # A report should never 500 because one value wasn't a number.
    assert render("{{ 'N/A'|peso }}") == "N/A"


def test_zero_formats_correctly(render):
    assert render("{{ 0|peso }}") == "₱0.00"
