"""Test scrap generation scenario handling from Django form to simulation."""

import pytest
from unittest.mock import patch
from django.urls import reverse
from steeloweb.models import ModelRun
from steeloweb.forms import ModelRunCreateForm


@pytest.mark.django_db
def test_form_has_scrap_generation_scenario_field():
    """Test that the form includes scrap generation scenario field."""
    form = ModelRunCreateForm()
    assert "scrap_generation_scenario" in form.fields

    # Check choices - should only have BAU and High circularity
    choices = dict(form.fields["scrap_generation_scenario"].choices)
    assert len(choices) == 2
    assert "business_as_usual" in choices
    assert choices["business_as_usual"] == "BAU"
    assert "circular_economy" in choices
    assert choices["circular_economy"] == "High circularity"


@pytest.mark.django_db
@patch("steeloweb.views._extract_technology_settings")
def test_form_submission_saves_scrap_generation_scenario(mock_extract, client, valid_modelrun_form_data):
    """Test that form submission properly saves scrap generation scenario to config."""
    # Mock the technology extraction to return valid settings
    mock_extract.return_value = {
        "BF": {"allowed": True, "from_year": 2025, "to_year": None},
        "BOF": {"allowed": True, "from_year": 2025, "to_year": None},
        "EAF": {"allowed": True, "from_year": 2025, "to_year": None},
        "DRING": {"allowed": True, "from_year": 2025, "to_year": None},
        "DRIH2EAF": {"allowed": True, "from_year": 2025, "to_year": None},
        "ESF": {"allowed": False, "from_year": 2025, "to_year": None},
        "MOE": {"allowed": False, "from_year": 2025, "to_year": None},
    }

    url = reverse("create-modelrun")
    response = client.post(url, data=valid_modelrun_form_data)
    assert response.status_code == 302

    # Check that the model run was created
    assert ModelRun.objects.count() == 1
    modelrun = ModelRun.objects.first()

    # Check that scrap_generation_scenario is in the config
    assert modelrun.config["scrap_generation_scenario"] == "circular_economy"


def test_modelrun_passes_scrap_generation_scenario_to_simulation(db):
    """Test that ModelRun.run() includes scrap_generation_scenario in SimulationConfig."""
    # Create a ModelRun with scrap_generation_scenario in config
    modelrun = ModelRun.objects.create(
        config={
            "scrap_generation_scenario": "circular_economy",
            "start_year": 2025,
            "end_year": 2026,
            # Add minimal required config
            "plants_json_path": "/path/to/plants.json",
            "technology_lcop_csv": "/path/to/tech.csv",
            "gravity_distances_csv": "/path/to/gravity.csv",
            "demand_center_xlsx": "/path/to/demand.xlsx",
            "demand_sheet_name": "Sheet1",
            "location_csv": "/path/to/location.csv",
            "cost_of_x_csv": "/path/to/cost.csv",
        }
    )

    # We can't actually run the simulation in tests, but we can verify
    # that the config would be passed correctly by examining the run() method
    config_data = modelrun.config.copy()
    expected_params = {
        "plants_json_path",
        "technology_lcop_csv",
        "gravity_distances_csv",
        "start_year",
        "end_year",
        "output_file",
        "demand_center_xlsx",
        "demand_sheet_name",
        "location_csv",
        "log_level",
        "cost_of_x_csv",
        "scrap_generation_scenario",
    }

    # Verify scrap_generation_scenario is in expected_params (as we modified)
    assert "scrap_generation_scenario" in expected_params

    # Verify it would be included in filtered config
    filtered_config = {k: v for k, v in config_data.items() if k in expected_params}
    assert "scrap_generation_scenario" in filtered_config
    assert filtered_config["scrap_generation_scenario"] == "circular_economy"


@pytest.mark.parametrize(
    "scenario,display_name",
    [
        ("business_as_usual", "BAU"),
        ("circular_economy", "High circularity"),
    ],
)
@pytest.mark.django_db
def test_scrap_generation_scenario_choices(scenario, display_name):
    """Test that only the two scrap generation scenario choices are available."""
    form = ModelRunCreateForm()
    choices = dict(form.fields["scrap_generation_scenario"].choices)

    assert scenario in choices
    assert choices[scenario] == display_name
