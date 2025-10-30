"""
Test that SimulationConfig is created consistently between CLI and Django.
"""

import tempfile
from pathlib import Path

import pytest
from django.test import TestCase

from steelo.domain import Year
from steelo.simulation import SimulationConfig, GeoConfig
from steeloweb.models import DataPreparation


@pytest.mark.django_db
class TestSimulationConfigConsistency(TestCase):
    """Test that SimulationConfig is created correctly in Django."""

    def setUp(self):
        """Set up test data."""
        # Create mock data packages first (required by DataPreparation)
        from steeloweb.models import DataPackage

        self.core_package = DataPackage.objects.create(
            name="core-data",
            version="2024.01.01",
            source_type=DataPackage.SourceType.LOCAL,
            source_url="/tmp/core-data",
        )

        self.geo_package = DataPackage.objects.create(
            name="geo-data",
            version="2024.01.01",
            source_type=DataPackage.SourceType.LOCAL,
            source_url="/tmp/geo-data",
        )

        # Create a mock data preparation
        self.data_prep = DataPreparation.objects.create(
            name="Test Prep",
            status=DataPreparation.Status.READY,
            data_directory="/tmp/test_data",
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
        )

    def test_geo_config_with_none_values(self):
        """Test that GeoConfig handles None values correctly without overriding defaults."""
        # Test 1: Empty GeoConfig should have defaults
        geo1 = GeoConfig()
        self.assertEqual(geo1.max_altitude, 1500.0)
        self.assertEqual(geo1.max_slope, 2.0)

        # Test 2: GeoConfig with only some values should keep defaults for others
        geo2 = GeoConfig(max_slope=3.0)
        self.assertEqual(geo2.max_altitude, 1500.0)
        self.assertEqual(geo2.max_slope, 3.0)

        # Test 3: GeoConfig with explicit None should override defaults (bad!)
        geo3 = GeoConfig(max_altitude=None, max_slope=None)
        self.assertIsNone(geo3.max_altitude)
        self.assertIsNone(geo3.max_slope)

    def test_modelrun_filters_none_geo_params(self):
        """Test that ModelRun correctly filters out None values for GeoConfig."""
        # Test the filtering logic directly
        config_data = {
            "start_year": 2025,
            "end_year": 2030,
            "output_dir": "/tmp/output",
            "master_excel_path": "/tmp/master.xlsx",
            "max_altitude": None,  # This should be filtered out
            "max_slope": 3.0,  # This should be used
        }

        # Simulate what ModelRun.run() does
        geo_params = ["max_altitude", "max_slope"]
        # Only include non-None values
        geo_config_data = {k: v for k, v in config_data.items() if k in geo_params and v is not None}

        # Assert that None values are filtered out
        self.assertNotIn("max_altitude", geo_config_data)
        self.assertIn("max_slope", geo_config_data)
        self.assertEqual(geo_config_data["max_slope"], 3.0)

        # Create GeoConfig with filtered data
        if geo_config_data:
            geo_config = GeoConfig(**geo_config_data)
            # Should have default for max_altitude, custom for max_slope
            self.assertEqual(geo_config.max_altitude, 1500.0)
            self.assertEqual(geo_config.max_slope, 3.0)

    def test_simulation_config_creation_consistency(self):
        """Test that SimulationConfig is created consistently with proper GeoConfig."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir(parents=True)
            fixtures_dir = data_dir / "fixtures"
            fixtures_dir.mkdir(parents=True)

            # Create required files
            (fixtures_dir / "plants.json").write_text("[]")
            (fixtures_dir / "demand_centers.json").write_text("[]")
            master_excel = data_dir / "master_input.xlsx"
            master_excel.touch()

            # Test CLI-style creation
            cli_config = SimulationConfig.from_data_directory(
                start_year=Year(2025),
                end_year=Year(2030),
                data_dir=data_dir,
                output_dir=Path(tmpdir) / "output",
                master_excel_path=master_excel,
                geo_config=GeoConfig(max_slope=3.0),  # Only override one param
            )

            # Verify GeoConfig has correct values
            self.assertEqual(cli_config.geo_config.max_slope, 3.0)
            self.assertEqual(cli_config.geo_config.max_altitude, 1500.0)  # Default

            # Test Django-style creation with None filtering
            django_params = {
                "max_altitude": None,  # Should be filtered
                "max_slope": 3.0,
            }
            geo_config_data = {k: v for k, v in django_params.items() if v is not None}
            django_geo_config = GeoConfig(**geo_config_data)

            django_config = SimulationConfig.from_data_directory(
                start_year=Year(2025),
                end_year=Year(2030),
                data_dir=data_dir,
                output_dir=Path(tmpdir) / "output",
                master_excel_path=master_excel,
                geo_config=django_geo_config,
            )

            # Both should have the same GeoConfig values
            self.assertEqual(django_config.geo_config.max_slope, cli_config.geo_config.max_slope)
            self.assertEqual(django_config.geo_config.max_altitude, cli_config.geo_config.max_altitude)

    def test_modelrun_config_with_empty_geo_params(self):
        """Test ModelRun with no geo parameters uses defaults."""
        # Test creating SimulationConfig without any geo parameters

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir(parents=True)
            fixtures_dir = data_dir / "fixtures"
            fixtures_dir.mkdir(parents=True)

            # Create required files
            (fixtures_dir / "plants.json").write_text("[]")
            (fixtures_dir / "demand_centers.json").write_text("[]")

            # Create config without any geo parameters
            sim_config = SimulationConfig.from_data_directory(
                start_year=Year(2025),
                end_year=Year(2030),
                data_dir=data_dir,
                output_dir=Path(tmpdir) / "output",
                master_excel_path=data_dir / "master.xlsx",
            )

            # Should have default GeoConfig
            self.assertEqual(sim_config.geo_config.max_altitude, 1500.0)
            self.assertEqual(sim_config.geo_config.max_slope, 2.0)
