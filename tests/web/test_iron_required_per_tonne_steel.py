"""Test the iron_required_per_tonne_steel setting from the Django form through to SimulationConfig."""

import tempfile
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.forms import DecimalField
from django.urls import reverse

from steeloweb.forms import ModelRunCreateForm
from steeloweb.models import DataPackage, DataPreparation, ModelRun


@pytest.mark.django_db
def test_field_exists_with_default():
    field = ModelRunCreateForm().fields.get("iron_required_per_tonne_steel")

    assert isinstance(field, DecimalField)
    assert field.initial == 1.2
    assert field.required is False


@pytest.mark.django_db
@pytest.mark.parametrize("value", [0, -1])
def test_field_rejects_non_positive_values(value):
    form = ModelRunCreateForm(data={"start_year": 2025, "end_year": 2030, "iron_required_per_tonne_steel": value})
    form.full_clean()

    assert "iron_required_per_tonne_steel" in form.errors


@pytest.mark.django_db
def test_create_page_renders_field(mock_technology_extraction, client):
    response = client.get(reverse("create-modelrun"))

    assert response.status_code == 200
    assert b'name="iron_required_per_tonne_steel"' in response.content
    assert b"id_iron_required_per_tonne_steel').value = '1.2'" in response.content


@pytest.mark.django_db
def test_post_saves_custom_value(mock_technology_extraction, client, valid_modelrun_form_data):
    form_data = {**valid_modelrun_form_data, "iron_required_per_tonne_steel": "1.35"}

    response = client.post(reverse("create-modelrun"), data=form_data)

    assert response.status_code == 302
    assert ModelRun.objects.latest("created_at").config["iron_required_per_tonne_steel"] == 1.35


@pytest.mark.django_db
def test_post_defaults_to_1_2_when_blank(mock_technology_extraction, client, valid_modelrun_form_data):
    form_data = {**valid_modelrun_form_data, "iron_required_per_tonne_steel": ""}

    response = client.post(reverse("create-modelrun"), data=form_data)

    assert response.status_code == 302
    assert ModelRun.objects.latest("created_at").config["iron_required_per_tonne_steel"] == 1.2


@pytest.mark.django_db
@patch("steelo.validation.validate_technology_settings")
@patch("steelo.bootstrap.bootstrap_simulation")
def test_run_passes_value_to_simulation_config(mock_bootstrap_simulation, mock_validate):
    """ModelRun.run() keeps the stored value when it builds the SimulationConfig."""
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA, version="test", source_type=DataPackage.SourceType.LOCAL
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA, version="test", source_type=DataPackage.SourceType.LOCAL
    )
    data_prep = DataPreparation.objects.create(
        name="Test Preparation",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory="/mock/data/dir",
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        with patch.object(settings, "MEDIA_ROOT", temp_dir):
            from django.core.files.base import ContentFile

            data_prep.master_excel_file.save("test_master.xlsx", ContentFile(b"fake excel content"), save=True)
            model_run = ModelRun.objects.create(
                name="Test Run",
                config={
                    "start_year": 2025,
                    "end_year": 2026,
                    "iron_required_per_tonne_steel": 1.35,
                    "technology_settings": {
                        "BF": {"allowed": True, "from_year": 2025, "to_year": None},
                        "BOF": {"allowed": True, "from_year": 2025, "to_year": None},
                        "EAF": {"allowed": True, "from_year": 2025, "to_year": None},
                        "DRING": {"allowed": True, "from_year": 2025, "to_year": None},
                    },
                },
                data_preparation=data_prep,
            )
            model_run.ensure_output_directories()

            mock_runner = MagicMock()
            mock_runner.run.return_value = {"status": "success"}
            mock_bootstrap_simulation.return_value = mock_runner

            model_run.run()

    mock_bootstrap_simulation.assert_called_once()
    config = mock_bootstrap_simulation.call_args.args[0]
    assert config.iron_required_per_tonne_steel == 1.35
