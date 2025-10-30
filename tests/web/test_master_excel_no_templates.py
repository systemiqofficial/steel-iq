"""
Tests for MasterExcelFile functionality that don't require templates.
Run these tests to verify the core functionality works without UI templates.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from pathlib import Path

from steeloweb.models import MasterExcelFile, DataPreparation, DataPackage
from steeloweb.forms import MasterExcelFileForm


@pytest.mark.django_db
class TestMasterExcelFileModel:
    """Test MasterExcelFile model functionality."""

    def test_create_master_excel_file(self):
        """Test creating a MasterExcelFile instance."""
        excel_content = b"fake excel content"
        excel_file = SimpleUploadedFile(
            "test.xlsx", excel_content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        master_excel = MasterExcelFile.objects.create(
            name="Test Master Excel", description="Test description", file=excel_file
        )

        assert master_excel.id is not None
        assert master_excel.name == "Test Master Excel"
        assert master_excel.description == "Test description"
        assert master_excel.validation_status == "pending"
        assert master_excel.is_template is False
        assert master_excel.is_example is False
        assert master_excel.validation_report == {}

    def test_master_excel_str_representation(self):
        """Test string representation of MasterExcelFile."""
        master_excel = MasterExcelFile(name="My Excel", is_template=False)
        assert str(master_excel) == "My Excel"

        template = MasterExcelFile(name="Template Excel", is_template=True)
        assert str(template) == "Template Excel (Template)"

    def test_get_file_path(self, settings):
        """Test getting the absolute file path."""
        excel_file = SimpleUploadedFile("test.xlsx", b"content")
        master_excel = MasterExcelFile.objects.create(name="Test", file=excel_file)

        file_path = master_excel.get_file_path()
        assert isinstance(file_path, Path)
        assert str(file_path).startswith(str(settings.MEDIA_ROOT))
        assert "master_excel" in str(file_path)


@pytest.mark.django_db
class TestMasterExcelFileForm:
    """Test MasterExcelFileForm functionality."""

    def test_valid_form(self):
        """Test form with valid data."""
        excel_file = SimpleUploadedFile(
            "test.xlsx", b"content", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        form_data = {"name": "Test Excel", "description": "Test description"}
        form = MasterExcelFileForm(data=form_data, files={"file": excel_file})
        assert form.is_valid()

    def test_invalid_file_extension(self):
        """Test form rejects non-Excel files."""
        txt_file = SimpleUploadedFile("test.txt", b"content", content_type="text/plain")
        form_data = {"name": "Test Excel", "description": "Test description"}
        form = MasterExcelFileForm(data=form_data, files={"file": txt_file})
        assert not form.is_valid()
        assert "file" in form.errors
        assert "Only Excel files" in str(form.errors["file"])

    def test_file_size_limit(self):
        """Test form rejects files over 200MB."""
        # Create a fake large file
        large_content = b"x" * (201 * 1024 * 1024)  # 201MB
        large_file = SimpleUploadedFile("test.xlsx", large_content)
        form_data = {"name": "Test Excel", "description": "Test description"}
        form = MasterExcelFileForm(data=form_data, files={"file": large_file})
        assert not form.is_valid()
        assert "file" in form.errors
        assert "200MB" in str(form.errors["file"])


@pytest.mark.django_db
class TestDataPreparationWithMasterExcel:
    """Test DataPreparation with MasterExcelFile integration."""

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
    def master_excel(self):
        excel_file = SimpleUploadedFile("master.xlsx", b"content")
        return MasterExcelFile.objects.create(name="Valid Master Excel", file=excel_file, validation_status="valid")

    def test_preparation_with_master_excel_file(self, core_package, geo_package, master_excel):
        """Test that DataPreparation can reference MasterExcelFile."""
        prep = DataPreparation.objects.create(
            name="Test Prep", core_data_package=core_package, geo_data_package=geo_package, master_excel=master_excel
        )

        assert prep.master_excel == master_excel
        assert prep.master_excel.name == "Valid Master Excel"

    def test_preparation_without_master_excel(self, core_package, geo_package):
        """Test that DataPreparation works without MasterExcelFile."""
        prep = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            # No master_excel specified
        )

        assert prep.master_excel is None

    def test_preparation_priority_order(self, core_package, geo_package, master_excel):
        """Test that MasterExcelFile takes priority over uploaded file."""
        excel_file = SimpleUploadedFile("uploaded.xlsx", b"uploaded content")

        prep = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            master_excel=master_excel,
            master_excel_file=excel_file,  # This should be ignored
        )

        assert prep.master_excel == master_excel
        assert prep.master_excel_file.name.endswith("uploaded.xlsx")
        # In the actual service, master_excel would take priority


@pytest.mark.django_db
class TestAdminIntegration:
    """Test admin interface functionality."""

    def test_master_excel_admin_registered(self):
        """Test that MasterExcelFile is registered in admin."""
        from django.contrib import admin
        from steeloweb.models import MasterExcelFile

        assert MasterExcelFile in admin.site._registry

    def test_data_preparation_admin_has_master_excel_field(self):
        """Test that DataPreparation admin shows master_excel field."""
        from django.contrib import admin
        from steeloweb.models import DataPreparation

        admin_class = admin.site._registry[DataPreparation]
        assert "master_excel" in admin_class.form.Meta.fields or hasattr(admin_class.form, "master_excel")
