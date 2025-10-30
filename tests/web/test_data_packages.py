"""
Tests for the data package management system.
"""

import pytest
from pathlib import Path
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch

from steeloweb.models import DataPackage, DataPreparation, ModelRun
from steeloweb.services import DataPreparationService


@pytest.mark.django_db
class TestDataPackageModel:
    """Test the DataPackage model."""

    def test_create_s3_package(self):
        """Test creating an S3-based data package."""
        package = DataPackage.objects.create(
            name="core-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/core-data-v1.0.0.zip",
            checksum="abc123",
            size_mb=10.5,
        )

        assert package.name == "core-data"
        assert package.version == "1.0.0"
        assert package.source_type == DataPackage.SourceType.S3
        assert package.get_file_path() is None  # S3 packages don't have local files
        assert str(package) == "Core Data v1.0.0 (S3)"

    def test_create_local_package(self):
        """Test creating a local file-based data package."""
        # Create a mock file
        mock_file = SimpleUploadedFile("test-data.zip", b"fake zip content", content_type="application/zip")

        package = DataPackage(
            name="core-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.LOCAL,
            local_file=mock_file,
        )

        # Mock the checksum calculation
        with patch.object(package, "calculate_checksum", return_value="mock_checksum"):
            package.save()

        assert package.source_type == DataPackage.SourceType.LOCAL
        assert package.local_file is not None
        assert package.checksum == "mock_checksum"
        assert package.size_mb == pytest.approx(0.0000152587890625, rel=1e-2)  # 16 bytes

    def test_unique_constraint(self):
        """Test that name+version must be unique."""
        DataPackage.objects.create(
            name="core-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/test.zip",
        )

        with pytest.raises(Exception):  # IntegrityError
            DataPackage.objects.create(
                name="core-data",
                version="1.0.0",
                source_type=DataPackage.SourceType.LOCAL,
            )


@pytest.mark.django_db
class TestDataPreparationModel:
    """Test the DataPreparation model."""

    def test_create_preparation(self):
        """Test creating a data preparation."""
        core_package = DataPackage.objects.create(
            name="core-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/core.zip",
        )
        geo_package = DataPackage.objects.create(
            name="geo-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/geo.zip",
        )

        prep = DataPreparation.objects.create(
            name="Test Preparation",
            core_data_package=core_package,
            geo_data_package=geo_package,
        )

        assert prep.name == "Test Preparation"
        assert prep.status == DataPreparation.Status.PENDING
        assert prep.core_data_package == core_package
        assert prep.geo_data_package == geo_package
        assert not prep.is_ready()

    def test_preparation_ready_state(self):
        """Test preparation ready state check."""
        prep = DataPreparation(name="Test", status=DataPreparation.Status.READY, data_directory="/path/to/data")
        assert prep.is_ready()

        prep.status = DataPreparation.Status.FAILED
        assert not prep.is_ready()

        prep.status = DataPreparation.Status.READY
        prep.data_directory = ""
        assert not prep.is_ready()

    def test_preparation_cleanup(self):
        """Test preparation cleanup method."""
        # Create required packages first
        core_package = DataPackage.objects.create(
            name="core-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/core.zip",
        )
        geo_package = DataPackage.objects.create(
            name="geo-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/geo.zip",
        )

        prep = DataPreparation.objects.create(
            name="Test",
            core_data_package=core_package,
            geo_data_package=geo_package,
            status=DataPreparation.Status.READY,
            data_directory="/fake/path",
        )

        with patch("shutil.rmtree") as mock_rmtree:
            with patch("pathlib.Path.exists", return_value=True):
                prep.cleanup()

        assert prep.data_directory == ""
        assert prep.status == DataPreparation.Status.PENDING
        mock_rmtree.assert_called_once()


@pytest.mark.django_db
class TestModelRunWithDataPreparation:
    """Test ModelRun integration with DataPreparation."""

    def test_modelrun_with_data_preparation(self):
        """Test that ModelRun can use a DataPreparation."""
        # Create packages
        core_package = DataPackage.objects.create(
            name="core-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/core.zip",
        )
        geo_package = DataPackage.objects.create(
            name="geo-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/geo.zip",
        )

        # Create preparation
        prep = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            status=DataPreparation.Status.READY,
            data_directory="/test/data",
        )

        # Create model run
        model_run = ModelRun.objects.create(data_preparation=prep, config={"start_year": 2025, "end_year": 2030})

        assert model_run.data_preparation == prep
        assert model_run.config["start_year"] == 2025


@pytest.mark.django_db
class TestDataPreparationService:
    """Test the data preparation service."""

    @patch("steeloweb.services.DataPreparationService.manager")
    @patch("tempfile.mkdtemp")
    @patch("shutil.move")
    def test_prepare_data_success(self, mock_move, mock_mkdtemp, mock_manager):
        """Test successful data preparation."""
        # Setup mocks
        mock_mkdtemp.return_value = "/tmp/test_prep"
        mock_manager.get_package_path.return_value = Path("/fake/package")

        # Create test data
        core_package = DataPackage.objects.create(
            name="core-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/core.zip",
        )
        geo_package = DataPackage.objects.create(
            name="geo-data",
            version="1.0.0",
            source_type=DataPackage.SourceType.S3,
            source_url="https://example.com/geo.zip",
        )

        prep = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
        )

        service = DataPreparationService()

        # Mock the underlying steelo service's prepare_data method
        from steelo.data import PreparationResult, PreparedFile, FileSource

        mock_result = PreparationResult()
        mock_result.total_duration = 1.0
        mock_result.files = [
            PreparedFile(
                filename="test.json",
                source=FileSource.CORE_DATA,
                source_detail="",
                duration=0.5,
                path=Path("/tmp/test.json"),
            )
        ]

        with patch.object(service.service, "prepare_data", return_value=mock_result):
            success, message = service.prepare_data(prep)

        assert success
        assert "successful" in message
        assert prep.status == DataPreparation.Status.READY
        assert prep.data_directory is not None

    def test_prepare_data_failure(self):
        """Test data preparation failure handling."""
        prep = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=DataPackage.objects.create(
                name="core-data",
                version="1.0.0",
                source_type=DataPackage.SourceType.S3,
                source_url="https://example.com/core.zip",
            ),
            geo_data_package=DataPackage.objects.create(
                name="geo-data",
                version="1.0.0",
                source_type=DataPackage.SourceType.S3,
                source_url="https://example.com/geo.zip",
            ),
        )

        service = DataPreparationService()

        # Force an error during data preparation
        with patch.object(service.service, "prepare_data", side_effect=Exception("Test error")):
            success, message = service.prepare_data(prep)

        assert not success
        assert "Test error" in message
        assert prep.status == DataPreparation.Status.FAILED
        assert prep.error_message == "Test error"


@pytest.mark.django_db
def test_import_data_packages_command():
    """Test the import_data_packages management command."""
    from django.core.management import call_command

    # Test importing from "S3" (mocked)
    call_command("import_data_packages", "--from-s3")

    # Check that packages were created
    assert DataPackage.objects.filter(name="core-data", source_type=DataPackage.SourceType.S3).exists()
    assert DataPackage.objects.filter(name="geo-data", source_type=DataPackage.SourceType.S3).exists()

    # Test that running again doesn't create duplicates
    initial_count = DataPackage.objects.count()
    call_command("import_data_packages", "--from-s3")
    assert DataPackage.objects.count() == initial_count
