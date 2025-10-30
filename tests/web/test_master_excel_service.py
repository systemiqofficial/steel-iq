"""
Test DataPreparationService integration with MasterExcelFile.
"""

import pytest
from unittest.mock import patch
from pathlib import Path
from django.core.files.uploadedfile import SimpleUploadedFile

from steeloweb.models import MasterExcelFile, DataPreparation, DataPackage
from steeloweb.services import DataPreparationService


@pytest.mark.django_db
class TestDataPreparationServiceWithMasterExcel:
    """Test DataPreparationService with MasterExcelFile integration."""

    @pytest.fixture
    def core_package(self):
        return DataPackage.objects.create(
            name="core-data", version="v1.0.0", source_type=DataPackage.SourceType.S3, source_url="s3://test/core.zip"
        )

    @pytest.fixture
    def geo_package(self):
        return DataPackage.objects.create(
            name="geo-data", version="v1.0.0", source_type=DataPackage.SourceType.S3, source_url="s3://test/geo.zip"
        )

    @pytest.fixture
    def master_excel(self, tmp_path):
        """Create a MasterExcelFile with an actual file."""
        # Create a temporary Excel file
        excel_path = tmp_path / "master.xlsx"
        excel_path.write_bytes(b"fake excel content")

        # Create the model instance
        with open(excel_path, "rb") as f:
            excel_file = SimpleUploadedFile("master.xlsx", f.read())
            return MasterExcelFile.objects.create(
                name="Test Master Excel",
                file=excel_file,
                validation_status="valid",
                validation_report={
                    "summary": {"error_count": 0, "warning_count": 1},
                    "errors": [],
                    "warnings": ["Test warning"],
                },
            )

    def test_service_uses_master_excel_file(self, core_package, geo_package, master_excel, tmp_path):
        """Test that service correctly uses MasterExcelFile when provided."""
        # Create DataPreparation with MasterExcelFile
        prep = DataPreparation.objects.create(
            name="Test Prep", core_data_package=core_package, geo_data_package=geo_package, master_excel=master_excel
        )

        # Create service
        service = DataPreparationService()

        # Mock the underlying steelo service
        from steelo.data import PreparationResult, PreparedFile, FileSource

        mock_result = PreparationResult()
        mock_result.total_duration = 1.0
        mock_result.files = [
            PreparedFile(
                filename="test.json",
                source=FileSource.MASTER_EXCEL,
                source_detail="Sheet1",
                duration=0.5,
                path=Path("/tmp/test.json"),
            )
        ]

        with (
            patch.object(service.service, "prepare_data", return_value=mock_result) as mock_prepare,
            patch("tempfile.mkdtemp", return_value="/tmp/test"),
            patch("shutil.move"),
        ):
            success, message = service.prepare_data(prep)

        # Verify success
        assert success is True
        assert prep.status == DataPreparation.Status.READY

        # Verify that the master excel file path was passed to the service
        mock_prepare.assert_called_once()
        call_args = mock_prepare.call_args
        assert call_args.kwargs.get("master_excel_path") is not None

    def test_service_falls_back_to_s3_without_master_excel(self, core_package, geo_package):
        """Test that service falls back to S3 when no MasterExcelFile is provided."""
        # Create DataPreparation without MasterExcelFile
        prep = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            # No master_excel specified
        )

        # Create service
        service = DataPreparationService()

        # Mock the underlying steelo service
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

        with (
            patch.object(service.service, "prepare_data", return_value=mock_result) as mock_prepare,
            patch("tempfile.mkdtemp", return_value="/tmp/test"),
            patch("shutil.move"),
        ):
            success, message = service.prepare_data(prep)

        # Verify success
        assert success is True

        # Verify that no master excel path was passed
        mock_prepare.assert_called_once()
        call_args = mock_prepare.call_args
        assert call_args.kwargs.get("master_excel_path") is None

    def test_master_excel_priority_order(self, core_package, geo_package, master_excel):
        """Test that MasterExcelFile takes priority over other sources."""
        # Create an uploaded file as well
        uploaded_file = SimpleUploadedFile("uploaded.xlsx", b"uploaded content")

        # Create DataPreparation with both MasterExcelFile and uploaded file
        prep = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            master_excel=master_excel,
            master_excel_file=uploaded_file,  # This should be ignored
        )

        service = DataPreparationService()

        # Mock the _log method to capture log messages
        log_messages = []
        original_log = service._log

        def mock_log(preparation, message):
            log_messages.append(message)
            return original_log(preparation, message)

        # Mock the underlying steelo service
        from steelo.data import PreparationResult

        mock_result = PreparationResult()
        mock_result.total_duration = 1.0
        mock_result.files = []

        with (
            patch.object(service, "_log", side_effect=mock_log),
            patch.object(service.service, "prepare_data", return_value=mock_result),
            patch("tempfile.mkdtemp", return_value="/tmp/test"),
            patch("shutil.move"),
        ):
            success, message = service.prepare_data(prep)

        # Verify that MasterExcelFile was used (not the uploaded file)
        assert any("Using MasterExcelFile: Test Master Excel" in msg for msg in log_messages)
        assert not any("Using uploaded master Excel file" in msg for msg in log_messages)
