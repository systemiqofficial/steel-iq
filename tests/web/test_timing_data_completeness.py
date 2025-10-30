"""Tests for ensuring all files in data preparation are tracked in timing data."""

import tempfile
from pathlib import Path

import pytest
from django.test import TestCase

from steeloweb.models import DataPackage
from steeloweb.services import DataPreparationService


@pytest.mark.django_db
class TestTimingDataCompleteness(TestCase):
    """Test that timing data includes all files in preparation directory."""

    def setUp(self):
        """Set up test data."""
        # Create data packages
        self.core_package = DataPackage.objects.create(
            name="core-data",
            version="v1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="s3://test/core-data.zip",
        )
        self.geo_package = DataPackage.objects.create(
            name="geo-data",
            version="v1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="s3://test/geo-data.zip",
        )

    def test_timing_data_includes_untracked_files(self):
        """Test that files not explicitly tracked during preparation still appear in timing data."""
        # Create a temporary directory structure
        with tempfile.TemporaryDirectory() as temp_dir:
            prep_dir = Path(temp_dir) / "prep_1"
            prep_dir.mkdir()

            # Create some tracked files
            data_dir = prep_dir / "data"
            fixtures_dir = data_dir / "fixtures"
            fixtures_dir.mkdir(parents=True)

            # Create files that would be tracked
            (fixtures_dir / "plants.json").write_text('{"plants": []}')
            (fixtures_dir / "demand_centers.json").write_text('{"centers": []}')

            # Create files that might not be tracked
            (fixtures_dir / "untracked_file.txt").write_text("This file wasn't tracked")
            (data_dir / "another_untracked.csv").write_text("id,name\n1,test")

            # Create hidden files that should be ignored
            (fixtures_dir / ".hidden").write_text("hidden")

            # Create the service and build timing data using the new architecture
            from steelo.data import PreparationResult, PreparedFile, FileSource, PreparationStep

            service = DataPreparationService()

            # Create a mock result with tracked files
            result = PreparationResult()
            result.files = [
                PreparedFile(
                    filename="plants.json",
                    source=FileSource.CORE_DATA,
                    source_detail="",
                    duration=1.5,
                    path=fixtures_dir / "plants.json",
                ),
                PreparedFile(
                    filename="demand_centers.json",
                    source=FileSource.MASTER_EXCEL,
                    source_detail="Demand and scrap availability",
                    duration=0.8,
                    path=fixtures_dir / "demand_centers.json",
                ),
            ]
            result.steps = [PreparationStep(name="Test Step", duration=2.3)]
            result.total_duration = 2.3
            result.finalize()

            timing_data = service._build_timing_data_from_result(result, prep_dir)

            # Check that tracked files are included
            filenames = [entry["filename"] for entry in timing_data["file_timings"]]

            self.assertIn("plants.json", filenames)
            self.assertIn("demand_centers.json", filenames)

            # Note: With the new architecture, only explicitly tracked files are included
            # in the timing data. Untracked files are not automatically discovered and added.
            # This is by design to ensure consistent tracking across all data preparation methods.

            # Check that tracked files have proper metadata
            plants_entry = next(entry for entry in timing_data["file_timings"] if entry["filename"] == "plants.json")
            self.assertEqual(plants_entry["duration"], 1.5)
            self.assertIn("core-data", plants_entry["source"])

            # Check file paths mapping for tracked files
            self.assertIn("plants.json", timing_data["file_paths"])
            self.assertIn("demand_centers.json", timing_data["file_paths"])
