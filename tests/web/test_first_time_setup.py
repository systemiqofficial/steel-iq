import pytest
from unittest.mock import patch, MagicMock
from django.urls import reverse

from steeloweb.models import DataPreparation, DataPackage, MasterExcelFile
from steelo.data.manager import DataManager


def _manifest_versions():
    manager = DataManager()
    core = manager.manifest.get_package("core-data")
    geo = manager.manifest.get_package("geo-data")

    def _normalize(pkg):
        version = pkg.version if pkg.version.startswith("v") else f"v{pkg.version}"
        return version, pkg.url

    core_version, core_url = _normalize(core)
    geo_version, geo_url = _normalize(geo)
    return core_version, core_url, geo_version, geo_url


@pytest.mark.django_db
class TestFirstTimeSetup:
    """Test the first-time setup flow for Electron app startup."""

    def test_shows_disclaimer_when_data_ready(self, client):
        """Test that first-time setup shows disclaimer when data is ready instead of redirecting immediately."""
        # Create a ready data preparation
        core_version, core_url, geo_version, geo_url = _manifest_versions()

        prep = DataPreparation.objects.create(
            name="Existing Data",
            status=DataPreparation.Status.READY,
            core_data_package=DataPackage.objects.create(
                name="core-data",
                version=core_version,
                source_type=DataPackage.SourceType.S3,
                source_url=core_url,
            ),
            geo_data_package=DataPackage.objects.create(
                name="geo-data",
                version=geo_version,
                source_type=DataPackage.SourceType.S3,
                source_url=geo_url,
            ),
        )

        response = client.get(reverse("first-time-setup"))

        # Should show the template with disclaimer, not redirect
        assert response.status_code == 200
        assert "steeloweb/first_time_setup.html" in [t.name for t in response.templates]
        assert response.context["preparation"] == prep
        assert response.context["is_new"] is False

        # Check for disclaimer content
        content = response.content.decode()
        assert "Legal Disclaimer for Public Use" in content
        assert "Get Started" in content
        assert "Setup Complete!" in content

    def test_get_started_button_redirects_to_main_app(self, client):
        """Test that clicking Get Started button redirects to main app."""
        # Create a ready data preparation
        core_version, core_url, geo_version, geo_url = _manifest_versions()

        DataPreparation.objects.create(
            name="Existing Data",
            status=DataPreparation.Status.READY,
            core_data_package=DataPackage.objects.create(
                name="core-data",
                version=core_version,
                source_type=DataPackage.SourceType.S3,
                source_url=core_url,
            ),
            geo_data_package=DataPackage.objects.create(
                name="geo-data",
                version=geo_version,
                source_type=DataPackage.SourceType.S3,
                source_url=geo_url,
            ),
        )

        # POST request simulating Get Started button click
        response = client.post(reverse("first-time-setup"))

        assert response.status_code == 302
        assert response.url == reverse("modelrun-list")

    def test_shows_progress_for_existing_preparation(self, client):
        """Test that first-time setup shows progress for an existing in-progress preparation."""
        # Create an in-progress data preparation
        core_version, core_url, geo_version, geo_url = _manifest_versions()

        prep = DataPreparation.objects.create(
            name="In Progress Data",
            status=DataPreparation.Status.DOWNLOADING,
            progress=45,
            core_data_package=DataPackage.objects.create(
                name="core-data",
                version=core_version,
                source_type=DataPackage.SourceType.S3,
                source_url=core_url,
            ),
            geo_data_package=DataPackage.objects.create(
                name="geo-data",
                version=geo_version,
                source_type=DataPackage.SourceType.S3,
                source_url=geo_url,
            ),
        )

        response = client.get(reverse("first-time-setup"))

        assert response.status_code == 200
        assert "steeloweb/first_time_setup.html" in [t.name for t in response.templates]
        assert response.context["preparation"] == prep
        assert response.context["is_new"] is False

    @patch("steeloweb.tasks.prepare_data_task")
    @patch("steeloweb.views._get_or_create_template_master_excel")
    def test_creates_new_preparation_when_none_exists(self, mock_get_template, mock_task, client):
        """Test that first-time setup creates a new preparation when none exists."""
        mock_task.enqueue = MagicMock()

        # Mock the template master Excel file to avoid S3 download
        mock_template = MasterExcelFile.objects.create(
            name="Master Input Template", description="Test template", is_template=True
        )
        mock_get_template.return_value = mock_template

        response = client.get(reverse("first-time-setup"))

        assert response.status_code == 200
        assert "steeloweb/first_time_setup.html" in [t.name for t in response.templates]

        # Check that a new preparation was created
        assert DataPreparation.objects.count() == 1
        prep = DataPreparation.objects.first()
        assert prep.name == "Initial Setup Data"
        assert prep.status == DataPreparation.Status.PENDING
        assert response.context["preparation"] == prep
        assert response.context["is_new"] is True

        # Check that the task was enqueued
        mock_task.enqueue.assert_called_once_with(prep.pk)

    @patch("steeloweb.tasks.prepare_data_task")
    @patch("steeloweb.views._get_or_create_template_master_excel")
    def test_creates_data_packages_if_not_exist(self, mock_get_template, mock_task, client):
        """Test that first-time setup creates data packages if they don't exist."""
        mock_task.enqueue = MagicMock()

        # Mock the template master Excel file to avoid S3 download
        mock_template = MasterExcelFile.objects.create(
            name="Master Input Template", description="Test template", is_template=True
        )
        mock_get_template.return_value = mock_template

        # Ensure no packages exist
        assert DataPackage.objects.count() == 0

        response = client.get(reverse("first-time-setup"))

        assert response.status_code == 200

        # Check that packages were created
        assert DataPackage.objects.count() == 2

        core_version, core_url, geo_version, geo_url = _manifest_versions()

        core_package = DataPackage.objects.get(name="core-data")
        assert core_package.version == core_version
        assert core_package.source_type == DataPackage.SourceType.S3
        assert core_package.source_url == core_url

        geo_package = DataPackage.objects.get(name="geo-data")
        assert geo_package.version == geo_version
        assert geo_package.source_type == DataPackage.SourceType.S3
        assert geo_package.source_url == geo_url

    def test_template_shows_welcome_message(self, client):
        """Test that the first-time setup template shows appropriate welcome content."""
        # Create a new preparation
        core_version, core_url, geo_version, geo_url = _manifest_versions()

        DataPreparation.objects.create(
            name="Initial Setup Data",
            status=DataPreparation.Status.DOWNLOADING,
            progress=25,
            core_data_package=DataPackage.objects.create(
                name="core-data",
                version=core_version,
                source_type=DataPackage.SourceType.S3,
                source_url=core_url,
            ),
            geo_data_package=DataPackage.objects.create(
                name="geo-data",
                version=geo_version,
                source_type=DataPackage.SourceType.S3,
                source_url=geo_url,
            ),
        )

        response = client.get(reverse("first-time-setup"))

        assert response.status_code == 200
        content = response.content.decode()

        # Check for key elements
        assert "Welcome to STEEL-IQ" in content
        assert "First Time Setup" in content
        assert "Setup Progress" in content
        assert "What's happening?" in content
        assert "25%" in content  # Progress percentage

    def test_disclaimer_content_complete(self, client):
        """Test that all disclaimer sections are present when data is ready."""
        # Create a ready data preparation
        core_version, core_url, geo_version, geo_url = _manifest_versions()

        DataPreparation.objects.create(
            name="Ready Data",
            status=DataPreparation.Status.READY,
            core_data_package=DataPackage.objects.create(
                name="core-data",
                version=core_version,
                source_type=DataPackage.SourceType.S3,
                source_url=core_url,
            ),
            geo_data_package=DataPackage.objects.create(
                name="geo-data",
                version=geo_version,
                source_type=DataPackage.SourceType.S3,
                source_url=geo_url,
            ),
        )

        response = client.get(reverse("first-time-setup"))
        content = response.content.decode()

        # Check all disclaimer sections are present
        assert "A. Disclaimer of Liability" in content
        assert "B. Restrictions on Use" in content
        assert "C. Confidentiality and Feedback" in content
        assert "D. Intellectual Property" in content
        assert "E. Data Protection" in content
        assert "UK GDPR" in content

    def test_template_includes_htmx_polling(self, client):
        """Test that the template includes HTMX polling for progress updates."""
        core_version, core_url, geo_version, geo_url = _manifest_versions()

        prep = DataPreparation.objects.create(
            name="Test Prep",
            status=DataPreparation.Status.PREPARING,
            core_data_package=DataPackage.objects.create(
                name="core-data",
                version=core_version,
                source_type=DataPackage.SourceType.S3,
                source_url=core_url,
            ),
            geo_data_package=DataPackage.objects.create(
                name="geo-data",
                version=geo_version,
                source_type=DataPackage.SourceType.S3,
                source_url=geo_url,
            ),
        )

        response = client.get(reverse("first-time-setup"))
        content = response.content.decode()

        # Check for HTMX attributes
        assert 'hx-get="' in content
        assert 'hx-trigger="load, every 2s"' in content
        assert f"/data-preparation/{prep.id}/progress/" in content  # The actual URL

    def test_shows_error_state(self, client):
        """Test that the template shows error state appropriately."""
        core_version, core_url, geo_version, geo_url = _manifest_versions()

        DataPreparation.objects.create(
            name="Failed Prep",
            status=DataPreparation.Status.FAILED,
            error_message="Test error message",
            core_data_package=DataPackage.objects.create(
                name="core-data",
                version=core_version,
                source_type=DataPackage.SourceType.S3,
                source_url=core_url,
            ),
            geo_data_package=DataPackage.objects.create(
                name="geo-data",
                version=geo_version,
                source_type=DataPackage.SourceType.S3,
                source_url=geo_url,
            ),
        )

        response = client.get(reverse("first-time-setup"))
        content = response.content.decode()

        # Check that error state is shown
        assert response.context["preparation"].status == DataPreparation.Status.FAILED
        assert "Test error message" in content  # Error message from the main template
        assert "check your internet connection" in content

    def test_shows_success_state_during_transition(self, client):
        """Test that the template shows success state when preparation just completed."""
        # This test simulates the case where the preparation becomes ready
        # AFTER the initial page load (via HTMX update)

        # Create a preparation that's "preparing" first
        core_version, core_url, geo_version, geo_url = _manifest_versions()

        prep = DataPreparation.objects.create(
            name="Almost Ready Prep",
            status=DataPreparation.Status.PREPARING,
            progress=95,
            core_data_package=DataPackage.objects.create(
                name="core-data",
                version=core_version,
                source_type=DataPackage.SourceType.S3,
                source_url=core_url,
            ),
            geo_data_package=DataPackage.objects.create(
                name="geo-data",
                version=geo_version,
                source_type=DataPackage.SourceType.S3,
                source_url=geo_url,
            ),
        )

        # Initial page load shows progress
        response = client.get(reverse("first-time-setup"))
        assert response.status_code == 200
        assert "steeloweb/first_time_setup.html" in [t.name for t in response.templates]

        # Now update status to ready and check what the template would show
        prep.status = DataPreparation.Status.READY
        prep.progress = 100
        prep.save()

        # The template now shows disclaimer instead of redirecting
        response = client.get(reverse("first-time-setup"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Legal Disclaimer for Public Use" in content
        assert "Get Started" in content
