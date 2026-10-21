"""Tests for the run configuration that the create form stores and ``ModelRun.run`` passes on."""

from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile

from steeloweb.models import DataPackage, DataPreparation, ModelRun


@pytest.mark.django_db
@patch("steelo.validation.validate_technology_settings")
@patch("steelo.bootstrap.bootstrap_simulation")
def test_run_passes_include_tariffs_to_the_simulation_config(mock_bootstrap_simulation, mock_validate, tmp_path):
    """An unticked "Include tariffs" box, stored as ``False``, reaches the ``SimulationConfig`` instead of its default."""
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
    model_run = ModelRun.objects.create(
        name="Tariffs off",
        config={
            "start_year": 2025,
            "end_year": 2026,
            "include_tariffs": False,
            "technology_settings": {"BF": {"allowed": True, "from_year": 2025, "to_year": None}},
        },
        data_preparation=data_prep,
    )

    model_run.run()

    config = mock_bootstrap_simulation.call_args[0][0]
    assert config.include_tariffs is False
