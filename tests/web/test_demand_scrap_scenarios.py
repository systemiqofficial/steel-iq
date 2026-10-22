"""Tests for the steel demand and scrap availability scenarios the run form picks from a data preparation."""

import json

import pytest
from django.urls import reverse

from steeloweb import forms
from steeloweb.models import ModelRun


def write_scenario_index(data_preparation, demand, scrap):
    """Give a ready preparation a scenario index holding the given names.

    Args:
        data_preparation: The ready ``DataPreparation``.
        demand: The demand scenario names.
        scrap: The scrap scenario names.
    """
    index = {
        "demand": {name: f"demand_scenarios/{name}/demand_centers.json" for name in demand},
        "scrap": {name: f"scrap_scenarios/{name}/suppliers.json" for name in scrap},
    }
    (data_preparation.get_data_path() / "data" / "fixtures" / "scenarios.json").write_text(json.dumps(index))


@pytest.mark.django_db
def test_run_form_offers_the_scenarios_of_the_preparation_index(client, ready_data_preparation):
    """The demand select offers the index's names starting at BAU, else the first; scrap adds "Same as demand" first.

    The fragment that refreshes the selects with the preparation keeps a choice the preparation offers.
    """
    write_scenario_index(ready_data_preparation, demand=["High growth", "BAU"], scrap=["BAU", "High recycling"])

    form = forms.ModelRunCreateForm()
    fragment = client.get(
        reverse("create-modelrun-demand-scrap-fragment"),
        {"data_preparation": ready_data_preparation.pk, "chosen_scrap_scenario": "High recycling"},
    )

    assert form.fields["chosen_demand_scenario"].choices == [("BAU", "BAU"), ("High growth", "High growth")]
    assert form.fields["chosen_demand_scenario"].initial == "BAU"
    assert form.fields["chosen_scrap_scenario"].choices == [
        ("", "Same as demand"),
        ("BAU", "BAU"),
        ("High recycling", "High recycling"),
    ]
    assert form.fields["chosen_scrap_scenario"].initial == ""
    assert fragment.status_code == 200
    assert fragment.context["form"]["chosen_demand_scenario"].value() == "BAU"
    assert fragment.context["form"]["chosen_scrap_scenario"].value() == "High recycling"

    write_scenario_index(ready_data_preparation, demand=["Low growth", "High growth"], scrap=["BAU"])

    assert forms.ModelRunCreateForm().fields["chosen_demand_scenario"].initial == "High growth"


@pytest.mark.django_db
def test_same_as_demand_stores_the_steel_demand_scenario_as_the_scrap_scenario(
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
    ready_data_preparation,
):
    """A post leaving the scrap scenario at "Same as demand" stores the chosen demand scenario for scrap too."""
    write_scenario_index(ready_data_preparation, demand=["BAU", "High growth"], scrap=["BAU", "High growth"])
    form_data = {**valid_modelrun_form_data, "chosen_demand_scenario": "High growth", "chosen_scrap_scenario": ""}

    response = client.post(reverse("create-modelrun"), data=form_data)

    assert response.status_code == 302
    config = ModelRun.objects.get().config
    assert (config["chosen_demand_scenario"], config["chosen_scrap_scenario"]) == ("High growth", "High growth")


@pytest.mark.django_db
def test_run_form_asks_to_prepare_again_for_a_preparation_without_the_scenario_index(
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
    ready_data_preparation,
):
    """A ready preparation made before run-time scenarios leaves the create page up and refuses a post naming it."""
    (ready_data_preparation.get_data_path() / "data" / "fixtures" / "scenarios.json").unlink()

    page = client.get(reverse("create-modelrun"))
    response = client.post(reverse("create-modelrun"), data=valid_modelrun_form_data)

    assert page.status_code == 200
    assert "prepare this master again" in page.content.decode()
    assert response.status_code == 200
    assert "prepare this master again" in response.context["form"].errors["chosen_demand_scenario"][0]
    assert not ModelRun.objects.exists()
