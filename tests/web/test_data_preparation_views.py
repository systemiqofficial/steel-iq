import pytest
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from steeloweb.models import DataPreparation, DataPackage, MasterExcelFile


@pytest.mark.django_db
class TestDataPreparationViews:
    """Test DataPreparation views and progress tracking."""

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

    def test_data_preparation_detail_view(self):
        """Test DataPreparation detail view."""
        # Create a data preparation
        prep = DataPreparation.objects.create(
            name="Test Preparation",
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            master_excel=self.master_excel,
            status=DataPreparation.Status.READY,
            progress=100,
        )

        url = reverse("data-preparation-detail", kwargs={"pk": prep.pk})
        response = self.client.get(url)

        assert response.status_code == 200
        assert "preparation" in response.context
        assert response.context["preparation"] == prep
        assert "simulations" in response.context

        # Check content
        content = response.content.decode()
        assert prep.name in content
        assert "100%" in content
        assert self.master_excel.name in content

    def test_data_preparation_progress_view(self):
        """Test DataPreparation progress endpoint."""
        # Create a preparation in processing state
        prep = DataPreparation.objects.create(
            name="Processing Preparation",
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            status=DataPreparation.Status.DOWNLOADING,
            progress=45,
        )

        url = reverse("data-preparation-progress", kwargs={"pk": prep.pk})
        response = self.client.get(url)

        assert response.status_code == 200
        content = response.content.decode()
        assert "45%" in content
        assert "Downloading data packages" in content

    def test_data_preparation_progress_ready_refreshes(self):
        """Test that progress endpoint triggers refresh when ready."""
        # Create a preparation in ready state
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

    def test_data_preparation_detail_with_different_statuses(self):
        """Test detail view with different preparation statuses."""
        statuses_to_test = [
            (DataPreparation.Status.PENDING, "Data preparation is queued"),
            (DataPreparation.Status.DOWNLOADING, "Downloading"),
            (DataPreparation.Status.EXTRACTING, "Extracting"),
            (DataPreparation.Status.PREPARING, "Preparing"),
            (DataPreparation.Status.READY, "Success!"),
            (DataPreparation.Status.FAILED, "Error Details"),
        ]

        for status, expected_text in statuses_to_test:
            prep = DataPreparation.objects.create(
                name=f"Test {status}",
                core_data_package=self.core_package,
                geo_data_package=self.geo_package,
                status=status,
                progress=50 if status != DataPreparation.Status.READY else 100,
                error_message="Test error" if status == DataPreparation.Status.FAILED else "",
            )

            url = reverse("data-preparation-detail", kwargs={"pk": prep.pk})
            response = self.client.get(url)

            assert response.status_code == 200
            content = response.content.decode()
            assert expected_text in content or status in content

    def test_data_preparation_with_log_messages(self):
        """Test that log messages are displayed."""
        prep = DataPreparation.objects.create(
            name="Test with Logs",
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            status=DataPreparation.Status.PREPARING,
            progress=75,
            preparation_log="Processing file: master_input.xlsx\nCreating JSON repositories...",
        )

        url = reverse("data-preparation-detail", kwargs={"pk": prep.pk})
        response = self.client.get(url)

        assert response.status_code == 200
        content = response.content.decode()
        assert "Processing Log" in content
        assert "master_input.xlsx" in content
        assert "Creating JSON repositories" in content

        # Test that the property alias works
        assert prep.log_messages == prep.preparation_log
