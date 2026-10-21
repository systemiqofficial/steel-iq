"""Tests for the run configuration that the create form stores and ``ModelRun.run`` passes on."""

from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from steelo import simulation
from steeloweb.models import DataPackage, DataPreparation, ModelRun


def ready_model_run(tmp_path, name, config):
    """A ``ModelRun`` with ``config`` on a ready data preparation, so ``run()`` gets as far as the simulation.

    Args:
        tmp_path: Folder for the preparation's data directory.
        name: The run's name.
        config: The run's stored config.

    Returns:
        The saved ``ModelRun``.
    """
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA,
        version="test",
        source_type=DataPackage.SourceType.LOCAL,
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA,
        version="test",
        source_type=DataPackage.SourceType.LOCAL,
    )
    data_prep = DataPreparation.objects.create(
        name="Test Preparation",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory=str(tmp_path / "prep"),
    )
    data_prep.master_excel_file.save("master.xlsx", ContentFile(b"fake excel content"), save=True)
    return ModelRun.objects.create(name=name, config=config, data_preparation=data_prep)


@pytest.mark.django_db
@patch("steelo.validation.validate_technology_settings")
@patch("steelo.bootstrap.bootstrap_simulation")
def test_run_passes_include_tariffs_to_the_simulation_config(mock_bootstrap_simulation, mock_validate, tmp_path):
    """An unticked "Include tariffs" box, stored as ``False``, reaches the ``SimulationConfig`` instead of its default."""
    model_run = ready_model_run(
        tmp_path,
        "Tariffs off",
        {
            "start_year": 2025,
            "end_year": 2026,
            "include_tariffs": False,
            "technology_settings": {"BF": {"allowed": True, "from_year": 2025, "to_year": None}},
        },
    )

    model_run.run()

    config = mock_bootstrap_simulation.call_args[0][0]
    assert config.include_tariffs is False


@pytest.mark.django_db
@patch("steelo.validation.validate_technology_settings")
@patch("steelo.bootstrap.bootstrap_simulation")
def test_run_drops_a_stored_scrap_generation_scenario(mock_bootstrap_simulation, mock_validate, tmp_path):
    """An older run's stored ``scrap_generation_scenario``, no longer a config field, does not stop the run."""
    model_run = ready_model_run(
        tmp_path,
        "Older run",
        {
            "start_year": 2025,
            "end_year": 2026,
            "scrap_generation_scenario": "circular_economy",
            "technology_settings": {"BF": {"allowed": True, "from_year": 2025, "to_year": None}},
        },
    )

    model_run.run()

    mock_bootstrap_simulation.assert_called_once()


@pytest.mark.django_db
def test_create_form_keeps_an_entered_zero(mock_technology_extraction, client, valid_modelrun_form_data):
    """A 0 typed into a field whose form allows 0 is stored as 0, not replaced by the field's default."""
    form_data = {
        **valid_modelrun_form_data,
        "hydrogen_ceiling_percentile": 0,
        "probability_of_announcement": 0,
    }

    response = client.post(reverse("create-modelrun"), data=form_data)

    assert response.status_code == 302
    config = ModelRun.objects.get().config
    assert config["hydrogen_ceiling_percentile"] == 0
    assert config["probability_of_announcement"] == 0


@pytest.mark.django_db
def test_create_form_records_the_plant_scope_with_clustering_off(
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
):
    """With clustering off, the stored clustering scope is "plant", the value the model then uses."""
    # An unticked box and the select it disables send no field
    form_data = {
        key: value
        for key, value in valid_modelrun_form_data.items()
        if key not in ("enable_furnace_group_clustering", "geographical_clustering_scope")
    }

    response = client.post(reverse("create-modelrun"), data=form_data)

    assert response.status_code == 302
    config = ModelRun.objects.get().config
    assert config["enable_furnace_group_clustering"] is False
    assert config["geographical_clustering_scope"] == "plant"


@pytest.mark.django_db
def test_create_form_stores_no_output_file_key(mock_technology_extraction, client, valid_modelrun_form_data):
    """A run created through the form carries no ``output_file`` key, which nothing reads."""
    response = client.post(reverse("create-modelrun"), data=valid_modelrun_form_data)

    assert response.status_code == 302
    assert "output_file" not in ModelRun.objects.get().config


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("posted", "stored"),
    [
        ("", simulation.config_default("global_risk_free_rate")),
        ("0", 0),
    ],
)
def test_create_form_stores_the_class_default_for_a_blank_field_and_an_entered_zero_as_zero(
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
    posted,
    stored,
):
    """A numeric field left blank stores the ``SimulationConfig`` default; the same field entered as 0 stores 0."""
    form_data = {**valid_modelrun_form_data, "global_risk_free_rate": posted}

    response = client.post(reverse("create-modelrun"), data=form_data)

    assert response.status_code == 302
    assert ModelRun.objects.get().config["global_risk_free_rate"] == stored
