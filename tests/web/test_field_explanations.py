"""Tests for the (?) explanations beside the run form's harder settings."""

from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django.utils.html import escape

from steelo import simulation
from steeloweb import forms

EXPLAINED_SETTINGS = {
    "capacity_policy_enabled",
    "capacity_policy_replacement_ratio",
    "capacity_policy_emission_intense_penalty_divisor",
    "capacity_policy_min_utilization_for_renovation",
    "capacity_policy_utilization_window_years",
    "capacity_policy_inter_company_swap_cutoff_year",
    "capacity_policy_banked_credit_rule",
    "capacity_policy_credit_validity_years",
    "capacity_policy_capacity_pool_max_retry_years",
    "capacity_policy_renovation_counts_as_replace",
    "chosen_emissions_boundary_for_carbon_costs",
    "carbon_border_export_rebates",
    "hydrogen_ceiling_percentile",
    "intraregional_trade_allowed",
    "geographical_clustering_scope",
    "top_n_loctechs_as_business_op",
    "pick_priority_sites_share",
    "consideration_time",
    "new_capacity_share_from_new_plants",
    "opening_balance_multiplier",
    "steel_price_buffer",
    "iron_price_buffer",
    "peg_iron_to_steel_price",
}


def explanation_icons(page):
    """The (?) icons of a page, each with whether it sits inside a ``<label>``.

    Args:
        page: The page's HTML.

    Returns:
        One ``(attributes, inside_label)`` pair per icon, in page order; the attribute values are unescaped.
    """
    icons = []
    open_labels = 0
    parser = HTMLParser()

    def handle_starttag(tag, attrs):
        nonlocal open_labels
        attributes = dict(attrs)
        if tag == "label":
            open_labels += 1
        elif "data-explanation-for" in attributes:
            icons.append((attributes, open_labels > 0))

    def handle_endtag(tag):
        nonlocal open_labels
        if tag == "label":
            open_labels -= 1

    parser.handle_starttag = handle_starttag
    parser.handle_endtag = handle_endtag
    parser.feed(page)
    parser.close()
    return icons


def test_every_explained_setting_and_no_other_field_has_an_explanation():
    """The explanations cover exactly the agreed settings, each a field of the run form."""
    assert set(forms.FIELD_EXPLANATIONS) == EXPLAINED_SETTINGS
    assert EXPLAINED_SETTINGS <= set(forms.ModelRunCreateForm.base_fields)


@pytest.mark.django_db
def test_create_page_renders_one_popover_per_explained_field(
    client,
    ready_data_preparation,
):
    """Each explained field gets one icon, outside every label, whose popover holds the field's escaped text."""
    page = client.get(reverse("create-modelrun")).content.decode()

    icons = explanation_icons(page)

    assert sorted(attributes["data-explanation-for"] for attributes, _ in icons) == sorted(EXPLAINED_SETTINGS)
    assert {
        attributes["data-explanation-for"]: attributes["data-bs-content"] for attributes, _ in icons
    } == forms.FIELD_EXPLANATIONS
    assert [
        name for name, text in forms.FIELD_EXPLANATIONS.items() if f'data-bs-content="{escape(text)}"' not in page
    ] == []
    assert [attributes["data-explanation-for"] for attributes, inside_label in icons if inside_label] == []


def test_explanations_hold_two_to_four_sentences():
    """Each text is two to four sentences ending in a full stop, the length agreed for the popovers."""
    sentence_counts = {name: len(text.split(". ")) for name, text in forms.FIELD_EXPLANATIONS.items()}

    assert {name: count for name, count in sentence_counts.items() if not 2 <= count <= 4} == {}
    assert [name for name, text in forms.FIELD_EXPLANATIONS.items() if not text.endswith(".")] == []


def test_linked_regions_explanation_lists_every_region_of_the_trade_matrix():
    """The intraregional trade popover has one bullet per link of the matrix, and names the unlinked regions."""
    matrix = simulation.config_default("geo_config.intraregional_trade_matrix")

    lines = forms.linked_regions_explanation().split("\n")

    bullets = {line.removeprefix("• ") for line in lines if line.startswith("• ")}
    expected = {
        " ↔ ".join(sorted((importer, exporter)))
        if importer in (matrix.get(exporter) or [])
        else f"{exporter} → {importer}"
        for importer, exporters in matrix.items()
        for exporter in exporters or []
    }
    assert bullets == expected
    assert lines[-1] == f"Regions without links: {', '.join(sorted(r for r, e in matrix.items() if not e))}."


def test_linked_regions_explanation_marks_a_one_way_link_with_an_arrow_to_the_importer(monkeypatch):
    """A link used one way only shows as exporter → importer."""
    monkeypatch.setattr(forms, "config_default", lambda name: {"Japan": ["South Korea"], "South Korea": None})

    text = forms.linked_regions_explanation()

    assert "• South Korea → Japan" in text.split("\n")
