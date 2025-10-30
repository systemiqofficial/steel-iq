import pytest
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch

from steeloweb.models import DataPreparation, DataPackage, MasterExcelFile
from steeloweb.services import DataPreparationService


@pytest.mark.django_db
class TestDataPreparationProgress:
    """Test DataPreparation progress tracking functionality."""

    @pytest.fixture(autouse=True)
    def setup(self, client):
        self.client = client

        # Create data packages
        self.core_package = DataPackage.objects.create(
            name="core-data", version="v1.0.3", source_type=DataPackage.SourceType.S3, source_url="s3://test/core.zip"
        )

        self.geo_package = DataPackage.objects.create(
            name="geo-data", version="v1.1.0", source_type=DataPackage.SourceType.S3, source_url="s3://test/geo.zip"
        )

        # Create master Excel
        excel_file = SimpleUploadedFile("test.xlsx", b"content")
        self.master_excel = MasterExcelFile.objects.create(
            name="Test Excel", file=excel_file, validation_status="valid"
        )

    def test_progress_updates_during_preparation(self):
        """Test that progress is updated during data preparation."""
        prep = DataPreparation.objects.create(
            name="Test Preparation",
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            master_excel=self.master_excel,
            status=DataPreparation.Status.PENDING,
        )

        service = DataPreparationService()

        # Track progress updates
        progress_updates = []

        def mock_update_progress(preparation, progress):
            progress_updates.append(progress)
            preparation.progress = progress
            preparation.save(update_fields=["progress"])

        # Mock the underlying steelo service
        from steelo.data import PreparationResult

        mock_result = PreparationResult()
        mock_result.total_duration = 1.0
        mock_result.files = []

        # Patch the actual preparation methods to prevent real processing
        with (
            patch.object(service.service, "prepare_data", return_value=mock_result),
            patch.object(service, "_update_progress", side_effect=mock_update_progress),
            patch("tempfile.mkdtemp", return_value="/tmp/test"),
            patch("shutil.move"),
            patch("shutil.rmtree"),
        ):
            success, message = service.prepare_data(prep)

            assert success
            # Check that progress was updated at various stages
            assert 5 in progress_updates  # Initial progress
            assert 20 in progress_updates  # Before starting preparation
            # The final 100% is set directly on the model, not through _update_progress
            assert prep.progress == 100
            assert prep.status == DataPreparation.Status.READY

    def test_progress_endpoint_triggers_refresh_on_ready(self):
        """Test that progress endpoint triggers page refresh when ready."""
        prep = DataPreparation.objects.create(
            name="Ready Preparation",
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            status=DataPreparation.Status.READY,
            progress=100,
        )

        url = reverse("data-preparation-progress", kwargs={"pk": prep.pk})
        response = self.client.get(url)

        # Should return template with success message and reload script
        assert response.status_code == 200
        assert "Data preparation completed successfully!" in response.content.decode()
        assert "window.location.reload()" in response.content.decode()

    def test_progress_endpoint_shows_status_specific_messages(self):
        """Test that progress endpoint shows appropriate messages for each status."""
        test_cases = [
            (DataPreparation.Status.DOWNLOADING, 20, "Downloading data packages"),
            (DataPreparation.Status.EXTRACTING, 40, "Extracting archives"),
            (DataPreparation.Status.PREPARING, 70, "Preparing JSON repositories"),
        ]

        for status, progress, expected_text in test_cases:
            prep = DataPreparation.objects.create(
                name=f"Test {status}",
                core_data_package=self.core_package,
                geo_data_package=self.geo_package,
                status=status,
                progress=progress,
            )

            url = reverse("data-preparation-progress", kwargs={"pk": prep.pk})
            response = self.client.get(url)

            assert response.status_code == 200
            content = response.content.decode()
            assert expected_text in content
            assert f"{progress}%" in content

    def test_prepare_data_updates_processing_time(self):
        """Test that processing time is recorded."""
        prep = DataPreparation.objects.create(
            name="Test Preparation",
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            master_excel=self.master_excel,
        )

        service = DataPreparationService()

        # Mock the underlying steelo service
        from steelo.data import PreparationResult

        mock_result = PreparationResult()
        mock_result.total_duration = 1.5
        mock_result.files = []

        # Mock all processing methods
        with (
            patch.object(service.service, "prepare_data", return_value=mock_result),
            patch("tempfile.mkdtemp", return_value="/tmp/test"),
            patch("shutil.move"),
            patch("time.sleep", side_effect=lambda x: None),
        ):  # Speed up test
            success, _ = service.prepare_data(prep)

            assert success
            prep.refresh_from_db()
            assert prep.processing_time is not None
            assert prep.processing_time > 0
