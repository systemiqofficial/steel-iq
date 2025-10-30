"""Test that ModelRun instances use isolated output directories to prevent conflicts."""

import tempfile
from pathlib import Path

import pytest

from steeloweb.models import ModelRun, DataPackage, DataPreparation


@pytest.mark.django_db
def test_modelrun_geo_output_isolation():
    """Test that each ModelRun uses its own isolated GEO output directory."""
    # Create data packages for the model runs
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA,
        version="1.0",
        source_type=DataPackage.SourceType.LOCAL,
        checksum="test_checksum",
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA,
        version="1.0",
        source_type=DataPackage.SourceType.LOCAL,
        checksum="test_checksum",
    )

    # Create data preparations
    data_prep1 = DataPreparation.objects.create(
        name="Test Data Prep 1",
        core_data_package=core_package,
        geo_data_package=geo_package,
        status=DataPreparation.Status.READY,
    )
    data_prep2 = DataPreparation.objects.create(
        name="Test Data Prep 2",
        core_data_package=core_package,
        geo_data_package=geo_package,
        status=DataPreparation.Status.READY,
    )

    # Create two ModelRun instances
    modelrun1 = ModelRun.objects.create(
        name="Test Run 1",
        data_preparation=data_prep1,
    )

    modelrun2 = ModelRun.objects.create(
        name="Test Run 2",
        data_preparation=data_prep2,
    )

    # Ensure output directories are created
    modelrun1.ensure_output_directories()
    modelrun2.ensure_output_directories()

    # Check that each ModelRun has its own output directory
    assert modelrun1.output_directory != modelrun2.output_directory
    assert modelrun1.output_directory != ""
    assert modelrun2.output_directory != ""

    # Check paths are properly isolated
    path1 = Path(modelrun1.output_directory)
    path2 = Path(modelrun2.output_directory)

    assert f"run_{modelrun1.pk}" in str(path1)
    assert f"run_{modelrun2.pk}" in str(path2)

    # Check that GEO directories are created and isolated
    geo_dir1 = path1 / "GEO"
    geo_dir2 = path2 / "GEO"

    # After the fix, GEO directories should be created
    assert geo_dir1.exists(), "GEO directory should be created"
    assert geo_dir2.exists(), "GEO directory should be created"

    # Ensure they are different directories
    assert geo_dir1 != geo_dir2, "Each ModelRun should have its own GEO directory"


@pytest.mark.django_db
def test_modelrun_creates_geo_subdirectories():
    """Test that ModelRun creates necessary GEO subdirectories when fixed."""
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA,
        version="1.0",
        source_type=DataPackage.SourceType.LOCAL,
        checksum="test_checksum",
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA,
        version="1.0",
        source_type=DataPackage.SourceType.LOCAL,
        checksum="test_checksum",
    )

    data_prep = DataPreparation.objects.create(
        name="Test Data Prep",
        core_data_package=core_package,
        geo_data_package=geo_package,
        status=DataPreparation.Status.READY,
    )

    modelrun = ModelRun.objects.create(
        name="Test Run",
        data_preparation=data_prep,
    )

    # Ensure output directories are created
    modelrun.ensure_output_directories()

    output_path = modelrun.get_output_path()

    # After implementing the fix, these directories should be created
    geo_dir = output_path / "GEO"
    baseload_sim_dir = geo_dir / "baseload_power_simulation"

    # These assertions will fail until we implement the fix
    assert geo_dir.exists(), f"GEO directory should exist: {geo_dir}"
    assert baseload_sim_dir.exists(), f"Baseload simulation directory should exist: {baseload_sim_dir}"


@pytest.mark.django_db
def test_modelrun_config_uses_isolated_geo_dirs():
    """Test that ModelRun config generation uses isolated GEO directories."""
    # Create a temporary directory structure to simulate data package
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create expected directory structure
        data_dir = temp_path / "data"
        data_dir.mkdir()
        fixtures_dir = data_dir / "fixtures"
        fixtures_dir.mkdir()

        # Create dummy fixture files
        (fixtures_dir / "plants.json").write_text("{}")
        (fixtures_dir / "demand_centers.json").write_text("{}")
        (fixtures_dir / "suppliers.json").write_text("{}")

        # Create data packages
        core_package = DataPackage.objects.create(
            name=DataPackage.PackageType.CORE_DATA,
            version="1.0",
            source_type=DataPackage.SourceType.LOCAL,
            checksum="test_checksum",
        )
        geo_package = DataPackage.objects.create(
            name=DataPackage.PackageType.GEO_DATA,
            version="1.0",
            source_type=DataPackage.SourceType.LOCAL,
            checksum="test_checksum",
        )

        # Create data preparation
        data_prep = DataPreparation.objects.create(
            name="Test Data Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            status=DataPreparation.Status.READY,
            data_directory=str(temp_path),
        )

        # Create ModelRun
        modelrun = ModelRun.objects.create(
            name="Test Run",
            data_preparation=data_prep,
            config={
                "start_year": 2025,
                "end_year": 2030,
            },
        )

        # Ensure output directories are created
        modelrun.ensure_output_directories()

        # Now we need to check if the config would use isolated directories
        # Since we can't easily access the internal config building, we'll check
        # that the necessary directories would be created
        output_path = modelrun.get_output_path()
        expected_geo_dir = output_path / "GEO"

        # This test verifies that the fix would create isolated GEO directories
        # Currently this will fail because GEO dirs are not created in ensure_output_directories
        assert expected_geo_dir.exists(), f"GEO directory should be created for isolation: {expected_geo_dir}"
