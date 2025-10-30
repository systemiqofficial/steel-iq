"""Test cases for the extended ModelRunCreateForm and CircularityDataForm."""

import json
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from steeloweb.forms import ModelRunCreateForm, CircularityDataForm
from steeloweb.models import ModelRun


@pytest.fixture
def minimal_form_data(ready_data_preparation):
    """Minimal valid form data for ModelRunCreateForm."""
    return {
        "data_preparation": ready_data_preparation.id,
        "start_year": 2025,
        "plant_lifetime": 20,
        "end_year": 2050,
        "total_steel_demand_scenario": "business_as_usual",
        "green_steel_demand_scenario": "business_as_usual",
        "scrap_generation_scenario": "business_as_usual",
        "circularity_file": "",
    }


@pytest.fixture
def extended_form_data(ready_data_preparation):
    """Extended form data with all fields populated."""
    return {
        "data_preparation": ready_data_preparation.id,
        "start_year": 2025,
        "plant_lifetime": 20,
        "end_year": 2050,
        "total_steel_demand_scenario": "high_efficiency",
        "green_steel_demand_scenario": "technology_breakthrough",
        "scrap_generation_scenario": "circular_economy",
        "circularity_file": "global_best_practice",
    }


@pytest.mark.django_db
def test_form_valid_with_minimal_data(minimal_form_data):
    """Test the form is valid with just the required fields."""
    form = ModelRunCreateForm(data=minimal_form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"


@pytest.mark.django_db
def test_form_valid_with_all_fields(extended_form_data):
    """Test the form is valid with all fields populated."""
    form = ModelRunCreateForm(data=extended_form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"


@pytest.mark.django_db
def test_form_invalid_with_end_year_before_start_year():
    """Test form is invalid when end_year is before start_year."""
    form_data = {
        "start_year": 2030,
        "end_year": 2025,
        "total_steel_demand_scenario": "accelerated_transition",
        "green_steel_demand_scenario": "high_efficiency",
        "scrap_generation_scenario": "circular_economy",
        "circularity_file": "high_scrap_availability",
    }
    form = ModelRunCreateForm(data=form_data)
    assert not form.is_valid()
    assert "End year must be after start year" in form.errors.get("__all__")[0]


@pytest.mark.django_db
def test_form_valid_with_same_start_and_end_year(ready_data_preparation):
    """Form should allow single-year simulations."""
    form_data = {
        "data_preparation": ready_data_preparation.id,
        "start_year": 2025,
        "plant_lifetime": 20,
        "end_year": 2025,
        "total_steel_demand_scenario": "business_as_usual",
        "green_steel_demand_scenario": "business_as_usual",
        "scrap_generation_scenario": "business_as_usual",
        "circularity_file": "",
    }
    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"


@pytest.mark.django_db
def test_post_modelrun_with_scenario_options(mock_technology_extraction, client, valid_modelrun_form_data):
    """Test POST to create modelrun saves the scenario options in the config."""
    # Update the fixture data with specific scenario values
    form_data = valid_modelrun_form_data.copy()
    form_data.update(
        {
            "total_steel_demand_scenario": "accelerated_transition",
            "green_steel_demand_scenario": "climate_neutrality",
            "scrap_generation_scenario": "circular_economy",
            "circularity_file": "eu_circular_economy",
            # Technology fields
            "bf_allowed": True,
            "bf_from_year": 2025,
            "bof_allowed": True,
            "bof_from_year": 2025,
            "dri_ng_allowed": True,
            "dri_ng_from_year": 2025,
            # DRI H2 fields removed
            # "dri_h2_allowed": False,
            # "dri_h2_from_year": 2025,
            "eaf_allowed": True,
            "eaf_from_year": 2025,
        }
    )

    response = client.post(reverse("create-modelrun"), data=form_data)
    assert response.status_code == 302  # Should redirect on success

    # Get the latest model run
    modelrun = ModelRun.objects.latest("started_at")

    # Check that the scenario options were saved in the config
    assert modelrun.config["total_steel_demand_scenario"] == "accelerated_transition"
    assert modelrun.config["green_steel_demand_scenario"] == "climate_neutrality"
    assert modelrun.config["scrap_generation_scenario"] == "circular_economy"
    assert modelrun.config["circularity_file"] == "eu_circular_economy"

    # Check technology fields in the new technology_settings structure
    tech_settings = modelrun.config.get("technology_settings", {})
    assert tech_settings.get("BF", {}).get("allowed") is True
    assert tech_settings.get("BF", {}).get("from_year") == 2025


@pytest.mark.django_db
def test_form_render_includes_demand_fields(mock_technology_extraction, client):
    """Test that the form renders with the new demand and circularity fields."""
    response = client.get(reverse("create-modelrun"))
    assert response.status_code == 200

    # Check that the new fields are in the response
    assert b'name="total_steel_demand_scenario"' in response.content
    assert b'name="green_steel_demand_scenario"' in response.content
    assert b'name="scrap_generation_scenario"' in response.content
    assert b'name="circularity_file"' in response.content

    # Check that the section headers are included
    assert b"Demand and circularity" in response.content
    assert b"Simulation Period" in response.content


# CircularityDataForm tests


@pytest.fixture
def valid_circularity_file():
    """Create a valid JSON file for circularity data."""
    json_content = json.dumps({"circularity": {"test": "data"}})
    return SimpleUploadedFile("circularity.json", bytes(json_content, "utf-8"), content_type="application/json")


def test_circularity_form_valid_with_required_data(valid_circularity_file):
    """Test the form is valid with required fields."""
    form_data = {
        "name": "Test Circularity Data",
    }

    form = CircularityDataForm(data=form_data, files={"circularity_file": valid_circularity_file})
    assert form.is_valid(), f"Form errors: {form.errors}"


def test_circularity_form_valid_with_all_fields(valid_circularity_file):
    """Test the form is valid with all fields populated."""
    form_data = {
        "name": "Test Circularity Data",
        "description": "Test description for circularity data",
    }

    form = CircularityDataForm(data=form_data, files={"circularity_file": valid_circularity_file})
    assert form.is_valid(), f"Form errors: {form.errors}"


def test_circularity_form_invalid_with_non_json_file():
    """Test form is invalid when file is not JSON."""
    form_data = {
        "name": "Test Circularity Data",
    }

    text_content = "This is not a JSON file"
    text_file = SimpleUploadedFile("circularity.txt", bytes(text_content, "utf-8"), content_type="text/plain")

    form = CircularityDataForm(data=form_data, files={"circularity_file": text_file})
    assert not form.is_valid()
    assert "Only JSON files are allowed" in form.errors.get("circularity_file")[0]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "scenario",
    [
        "business_as_usual",
        "accelerated_transition",
        "climate_neutrality",
        "circular_economy",
        "high_efficiency",
        "technology_breakthrough",
    ],
)
def test_demand_scenario_choices_exist(scenario):
    """Test that expected scenario choices are available in demand fields."""
    form = ModelRunCreateForm()

    # Check only the total and green steel demand fields (not scrap_generation)
    for field_name in ["total_steel_demand_scenario", "green_steel_demand_scenario"]:
        choices = dict(form.fields[field_name].choices)
        assert scenario in choices, f"{scenario} not found in {field_name} choices"


@pytest.mark.django_db
def test_scrap_generation_has_limited_choices():
    """Test that scrap_generation_scenario only has BAU and High circularity options."""
    form = ModelRunCreateForm()
    choices = dict(form.fields["scrap_generation_scenario"].choices)

    # Should only have 2 choices
    assert len(choices) == 2
    assert "business_as_usual" in choices
    assert choices["business_as_usual"] == "BAU"
    assert "circular_economy" in choices
    assert choices["circular_economy"] == "High circularity"
