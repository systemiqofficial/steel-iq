"""Tests for the "Return to default settings" button of each run form section."""

import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import RequestFactory
from django.urls import reverse

from steeloweb import forms, views

RESET_BUTTON = "Return to default settings"


def section_has_reset_button(page):
    """Whether each card section of a page holds a reset button.

    Args:
        page: The page's HTML.

    Returns:
        Each section title mapped to whether a reset button sits between its header and the next section's.
    """
    sections = re.split(r'<h5 class="mb-0">([^<]+)</h5>', page)
    return {title: RESET_BUTTON in body for title, body in zip(sections[1::2], sections[2::2])}


@pytest.mark.django_db
def test_create_page_gives_every_settings_section_a_reset_button(
    client,
    ready_data_preparation,
):
    """Every section holding settings has a reset button; the simulation name, which has no default, has none."""
    page = client.get(reverse("create-modelrun")).content.decode()
    technology_table = client.get(
        reverse("create-modelrun-tech-fragment"),
        {"data_preparation": ready_data_preparation.id},
    ).content.decode()

    # The technology table loads into its placeholder section afterwards, so it is checked on its own
    sections = section_has_reset_button(page)
    del sections["Technology Configuration"]
    assert sections == {
        "Simulation Name": False,
        "Simulation Parameters": True,
        "Simulation Period": True,
        "Data Input": True,
        "Plant Construction Settings": True,
        "Economic Parameters": True,
        "Policy Settings": True,
        "Geospatial Settings": True,
    }
    assert section_has_reset_button(technology_table) == {"Technology Configuration": True}


@pytest.mark.django_db
def test_scenario_and_capacity_policy_fields_carry_their_defaults_for_the_reset_buttons():
    """The scenario selects and the capacity policy levers hold their initials, booleans lower case and None empty."""
    fields = forms.ModelRunCreateForm().fields

    assert fields["chosen_scrap_scenario"].widget.attrs["data-default"] == ""
    assert fields["capacity_policy_replacement_ratio"].widget.attrs["data-default"] == "1.5"
    assert fields["capacity_policy_renovation_counts_as_replace"].widget.attrs["data-default"] == "true"
    assert fields["capacity_policy_no_swap_cutoff"].widget.attrs["data-default"] == "false"
    assert fields["capacity_policy_credit_validity_years"].widget.attrs["data-default"] == ""


def test_technology_defaults_keep_the_preparation_values():
    """The reset defaults of a technology are its preparation's allowed flag and end year."""
    technologies = {"bf": {"code": "BF", "allowed": False, "from_year": 2025, "to_year": 2040}}

    views._enrich_technology_defaults(technologies)

    assert technologies["bf"]["default_allowed"] is False
    assert technologies["bf"]["default_to_year"] == 2040
    assert technologies["bf"]["default_from_year"] == 2025


def test_technology_defaults_survive_posted_values_after_a_validation_error():
    """A form sent back with errors shows the posted values, while the reset defaults stay the preparation's."""
    preparation = Mock()
    preparation.get_technologies.return_value = {
        "bf": {"code": "BF", "allowed": True, "from_year": 2025, "to_year": None},
    }
    request = RequestFactory().post("/", {"tech_bf_from_year": "2035", "tech_bf_to_year": "2050"})

    context = views._build_validation_error_context(request, SimpleNamespace(), preparation)

    bf = context["technologies"]["bf"]
    assert (bf["allowed"], bf["from_year"], bf["to_year"]) == (False, 2035, 2050)
    assert (bf["default_allowed"], bf["default_from_year"], bf["default_to_year"]) == (True, 2025, None)
