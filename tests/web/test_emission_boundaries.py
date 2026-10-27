"""Tests for the emissions boundaries a data preparation offers the run form."""

import json

import pytest
from django.urls import reverse

from steeloweb.models import DataPreparation, ModelRun


@pytest.mark.django_db
def test_get_emission_boundaries_returns_each_boundary_once_sorted_and_fails_without_the_file(tmp_path):
    """Each ``boundary`` of the prepared emission factors comes back once, sorted; a preparation without the file raises."""
    fixtures = tmp_path / "data" / "fixtures"
    fixtures.mkdir(parents=True)
    records = [
        {"boundary": boundary}
        for boundary in ("worldsteel_opt_credits", "rs-inspired", "worldsteel_opt_credits", "worldsteel_no_opt_credits")
    ]
    (fixtures / "technology_emission_factors.json").write_text(json.dumps(records))

    boundaries = DataPreparation(data_directory=str(tmp_path)).get_emission_boundaries()

    assert boundaries == ["rs-inspired", "worldsteel_no_opt_credits", "worldsteel_opt_credits"]
    with pytest.raises(FileNotFoundError):
        DataPreparation(data_directory=str(tmp_path / "unprepared")).get_emission_boundaries()


@pytest.mark.django_db
def test_create_page_stays_up_and_refuses_a_preparation_without_emission_factors(
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
    ready_data_preparation,
):
    """A ready preparation missing its emission factors leaves the create page up and refuses a post naming it."""
    (ready_data_preparation.get_data_path() / "data" / "fixtures" / "technology_emission_factors.json").unlink()

    page = client.get(reverse("create-modelrun"))
    response = client.post(reverse("create-modelrun"), data=valid_modelrun_form_data)

    assert page.status_code == 200
    assert response.status_code == 200
    assert (
        "prepare the master again" in response.context["form"].errors["chosen_emissions_boundary_for_carbon_costs"][0]
    )
    assert not ModelRun.objects.exists()
