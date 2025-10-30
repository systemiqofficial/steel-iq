"""Tests for the prepared file viewing functionality."""

import json
import tempfile
from pathlib import Path

import pytest
from django.test import TestCase
from django.urls import reverse

from steeloweb.models import DataPreparation, DataPackage


@pytest.mark.django_db
class TestPreparedFileView(TestCase):
    """Test viewing prepared data files."""

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

        # Create a temporary directory for test data
        self.temp_dir = tempfile.mkdtemp()
        self.fixtures_dir = Path(self.temp_dir) / "data" / "fixtures"
        self.fixtures_dir.mkdir(parents=True)

        # Create test JSON files
        self.small_json_data = {"name": "test", "items": [1, 2, 3]}
        with open(self.fixtures_dir / "small.json", "w") as f:
            json.dump(self.small_json_data, f)

        # Create a large JSON that's over 100KB
        self.large_json_data = {
            "plants": [{"id": i, "name": f"Plant {i}", "description": "x" * 1000} for i in range(200)]
        }
        with open(self.fixtures_dir / "large.json", "w") as f:
            json.dump(self.large_json_data, f)

        # Create test CSV file
        with open(self.fixtures_dir / "test.csv", "w") as f:
            f.write("id,name\n1,Test\n")

        # Create ready preparation
        self.preparation = DataPreparation.objects.create(
            name="Test Preparation",
            status=DataPreparation.Status.READY,
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            data_directory=self.temp_dir,
            timing_data={
                "step_timings": [{"name": "Test", "duration": 1.0, "percentage": 100}],
                "file_timings": [
                    {"filename": "small.json", "source": "test", "duration": 0.5},
                    {"filename": "large.json", "source": "test", "duration": 0.3},
                    {"filename": "test.csv", "source": "test", "duration": 0.2},
                ],
                "total_time": 1.0,
            },
        )

    def tearDown(self):
        """Clean up test data."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_view_small_json_file(self):
        """Test viewing a small JSON file shows full content."""
        url = reverse("view-prepared-file", kwargs={"pk": self.preparation.pk, "filename": "small.json"})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "steeloweb/prepared_file_view.html")
        self.assertContains(response, "small.json")
        self.assertNotContains(response, "Preview Mode")
        self.assertFalse(response.context["is_large"])

    def test_view_large_json_file(self):
        """Test viewing a large JSON file shows preview."""
        url = reverse("view-prepared-file", kwargs={"pk": self.preparation.pk, "filename": "large.json"})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "large.json")
        self.assertContains(response, "Preview Mode")
        self.assertTrue(response.context["is_large"])
        self.assertContains(response, "Total plants items: 200")

    def test_download_json_file(self):
        """Test downloading a JSON file."""
        url = reverse("view-prepared-file", kwargs={"pk": self.preparation.pk, "filename": "small.json"})
        response = self.client.get(url + "?download=1")

        self.assertEqual(response.status_code, 200)
        # Django FileResponse sets content type based on file extension
        self.assertIn(response["Content-Type"], ["application/json", "application/octet-stream"])
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="small.json"')

    def test_view_csv_file_inline(self):
        """Test that CSV files are shown inline."""
        url = reverse("view-prepared-file", kwargs={"pk": self.preparation.pk, "filename": "test.csv"})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "steeloweb/prepared_file_view_csv.html")
        self.assertContains(response, "test.csv")

    def test_view_nonexistent_file(self):
        """Test viewing a nonexistent file returns 404."""
        url = reverse("view-prepared-file", kwargs={"pk": self.preparation.pk, "filename": "nonexistent.json"})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

    def test_view_file_preparation_not_ready(self):
        """Test viewing files when preparation is not ready."""
        self.preparation.status = DataPreparation.Status.PENDING
        self.preparation.save()

        url = reverse("view-prepared-file", kwargs={"pk": self.preparation.pk, "filename": "small.json"})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)  # Redirect
        self.assertRedirects(response, reverse("data-preparation-detail", kwargs={"pk": self.preparation.pk}))

    def test_security_path_traversal(self):
        """Test that path traversal attempts are blocked."""
        url = reverse("view-prepared-file", kwargs={"pk": self.preparation.pk, "filename": "test.json"})
        # Manually modify the URL to include path traversal
        url = url.replace("test.json", "..%2F..%2F..%2Fetc%2Fpasswd")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)
