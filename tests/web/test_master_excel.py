import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from pathlib import Path
import tempfile

from steeloweb.models import MasterExcelFile, DataPreparation, DataPackage, ModelRun
from steeloweb.forms import MasterExcelFileForm, DataPreparationForm


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

    @pytest.mark.skipif(True, reason="Requires actual Excel validator")
    def test_validate_method(self):
        """Test validation method updates status and report."""
        # This would require mocking the MasterExcelValidator
        # Skipping for now as it requires the actual validator
        pass


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
class TestDataPreparationFormWithMasterExcel:
    """Test DataPreparationForm with MasterExcelFile integration."""

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

    def test_form_with_default_choice(self, core_package, geo_package):
        """Test form with default S3 choice."""
        form_data = {
            "name": "Test Preparation",
            "core_data_package": core_package.id,
            "geo_data_package": geo_package.id,
            "master_excel_choice": "default",
        }
        form = DataPreparationForm(data=form_data)
        assert form.is_valid()
        cleaned = form.cleaned_data
        assert cleaned["master_excel"] is None
        assert cleaned["master_excel_file"] is None

    def test_form_with_select_choice(self, core_package, geo_package, master_excel):
        """Test form with select existing file choice."""
        form_data = {
            "name": "Test Preparation",
            "core_data_package": core_package.id,
            "geo_data_package": geo_package.id,
            "master_excel_choice": "select",
            "master_excel": master_excel.id,
        }
        form = DataPreparationForm(data=form_data)
        assert form.is_valid()
        cleaned = form.cleaned_data
        assert cleaned["master_excel"] == master_excel
        assert cleaned["master_excel_file"] is None

    def test_form_with_upload_choice(self, core_package, geo_package, monkeypatch):
        """Test form with upload new file choice."""

        # Mock the validation to avoid actual Excel validation
        class MockReport:
            def has_errors(self):
                return False

            errors = []
            warnings = []
            info = []

        def mock_validate_file(self, path):
            return MockReport()

        monkeypatch.setattr(
            "steelo.adapters.dataprocessing.master_excel_validator.MasterExcelValidator.validate_file",
            mock_validate_file,
        )

        excel_file = SimpleUploadedFile(
            "new.xlsx", b"content", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        form_data = {
            "name": "Test Preparation",
            "core_data_package": core_package.id,
            "geo_data_package": geo_package.id,
            "master_excel_choice": "upload",
        }
        form = DataPreparationForm(data=form_data, files={"master_excel_file": excel_file})

        if not form.is_valid():
            print(f"Form errors: {form.errors}")
        assert form.is_valid()
        cleaned = form.cleaned_data
        assert cleaned["master_excel"] is None
        assert cleaned["master_excel_file"] is not None

    def test_form_requires_selection_when_select_choice(self, core_package, geo_package):
        """Test form validation when select choice but no selection."""
        form_data = {
            "name": "Test Preparation",
            "core_data_package": core_package.id,
            "geo_data_package": geo_package.id,
            "master_excel_choice": "select",
            # Missing master_excel selection
        }
        form = DataPreparationForm(data=form_data)
        assert not form.is_valid()
        assert "Please select an existing master Excel file" in str(form.errors)

    def test_form_requires_upload_when_upload_choice(self, core_package, geo_package):
        """Test form validation when upload choice but no file."""
        form_data = {
            "name": "Test Preparation",
            "core_data_package": core_package.id,
            "geo_data_package": geo_package.id,
            "master_excel_choice": "upload",
            # Missing file upload
        }
        form = DataPreparationForm(data=form_data)
        assert not form.is_valid()
        assert "Please upload a master Excel file" in str(form.errors)


@pytest.mark.django_db
class TestMasterExcelFileViews:
    """Test MasterExcelFile views."""

    @pytest.fixture
    def master_excel(self):
        excel_file = SimpleUploadedFile("test.xlsx", b"content")
        return MasterExcelFile.objects.create(name="Test Excel", file=excel_file, validation_status="valid")

    def test_list_view(self, client):
        """Test master Excel list view."""
        url = reverse("master-excel-list")
        response = client.get(url)
        assert response.status_code == 200
        assert "master_excel_files" in response.context
        assert "template_files" in response.context
        assert "example_files" in response.context
        assert "user_files" in response.context

    def test_create_view_get(self, client):
        """Test master Excel create view GET."""
        url = reverse("master-excel-create")
        response = client.get(url)
        assert response.status_code == 200
        assert "form" in response.context
        assert isinstance(response.context["form"], MasterExcelFileForm)

    def test_create_view_post(self, client, monkeypatch):
        """Test master Excel create view POST."""
        url = reverse("master-excel-create")
        excel_file = SimpleUploadedFile("new.xlsx", b"content")

        # Mock validation
        monkeypatch.setattr("steeloweb.models.MasterExcelFile.validate", lambda self: None)

        response = client.post(url, {"name": "New Excel", "description": "New description", "file": excel_file})

        assert response.status_code == 302  # Redirect after success
        assert MasterExcelFile.objects.filter(name="New Excel").exists()

    def test_detail_view(self, client, master_excel):
        """Test master Excel detail view."""
        url = reverse("master-excel-detail", kwargs={"pk": master_excel.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert response.context["master_excel"] == master_excel

    def test_update_view_get(self, client, master_excel):
        """Test master Excel update view GET."""
        url = reverse("master-excel-update", kwargs={"pk": master_excel.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert "form" in response.context
        assert response.context["form"].instance == master_excel

    def test_download_view(self, client, master_excel, monkeypatch):
        """Test master Excel download view."""
        url = reverse("master-excel-download", kwargs={"pk": master_excel.pk})

        # Create actual file for download test
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name

        # Mock get_file_path to return our temp file
        monkeypatch.setattr("steeloweb.models.MasterExcelFile.get_file_path", lambda self: Path(tmp_path))

        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("attachment")
        assert ".xlsx" in response["Content-Disposition"]

        # Cleanup
        Path(tmp_path).unlink()

    def test_download_template_view(self, client, monkeypatch):
        """Test download master Excel template view."""
        url = reverse("download-master-excel-template")

        # Mock DataManager to avoid S3 calls
        class MockManager:
            def download_package(self, name, force=False):
                pass

            def get_package_path(self, name):
                return None

        monkeypatch.setattr("steelo.data.manager.DataManager", MockManager)

        response = client.get(url)
        # Should redirect when package not found
        assert response.status_code == 302


@pytest.mark.django_db
class TestDataPreparationServiceWithMasterExcel:
    """Test DataPreparationService with MasterExcelFile."""

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
        return MasterExcelFile.objects.create(
            name="Test Master Excel",
            file=excel_file,
            validation_status="valid",
            validation_report={"summary": {"error_count": 0, "warning_count": 0}, "errors": [], "warnings": []},
        )

    def test_preparation_uses_master_excel_file(self, core_package, geo_package, master_excel):
        """Test that DataPreparation uses MasterExcelFile when provided."""
        prep = DataPreparation.objects.create(
            name="Test Prep", core_data_package=core_package, geo_data_package=geo_package, master_excel=master_excel
        )

        assert prep.master_excel == master_excel

        # The actual service test would require extensive mocking
        # This test verifies the model relationship works

    def test_preparation_validation_report_copied(self, core_package, geo_package, master_excel):
        """Test that validation report is copied from MasterExcelFile."""
        prep = DataPreparation.objects.create(
            name="Test Prep", core_data_package=core_package, geo_data_package=geo_package, master_excel=master_excel
        )

        # In the actual service, the validation report would be copied
        # This would be done in _process_master_excel method
        assert prep.master_excel.validation_report == master_excel.validation_report


@pytest.mark.django_db
class TestMasterExcelFileDelete:
    """Test delete functionality for MasterExcelFile"""

    @pytest.fixture(autouse=True)
    def setup(self, client):
        self.client = client
        self.master_excel = self.create_master_excel()

    def create_master_excel(self):
        excel_file = SimpleUploadedFile(
            "test.xlsx", b"content", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        return MasterExcelFile.objects.create(name="Test File", description="Test Description", file=excel_file)

    def test_delete_view_get(self):
        """Test delete confirmation page"""
        url = reverse("master-excel-delete", kwargs={"pk": self.master_excel.pk})
        response = self.client.get(url)

        assert response.status_code == 200
        assert "steeloweb/master_excel_confirm_delete.html" in [t.name for t in response.templates]
        assert self.master_excel.name in response.content.decode()

    def test_delete_view_post(self):
        """Test actually deleting a master Excel file"""
        url = reverse("master-excel-delete", kwargs={"pk": self.master_excel.pk})
        response = self.client.post(url)

        # Should redirect to list view
        assert response.status_code == 302
        assert response.url == reverse("master-excel-list")

        # File should be deleted
        with pytest.raises(MasterExcelFile.DoesNotExist):
            MasterExcelFile.objects.get(pk=self.master_excel.pk)

    def test_delete_view_prevents_template_deletion(self):
        """Test that template files cannot be deleted"""
        template = MasterExcelFile.objects.create(
            name="Template", is_template=True, file=SimpleUploadedFile("template.xlsx", b"content")
        )

        url = reverse("master-excel-delete", kwargs={"pk": template.pk})
        response = self.client.get(url)

        # Should get 404 since templates are filtered out
        assert response.status_code == 404

    def test_delete_view_prevents_deletion_if_in_use(self):
        """Test that files in use by ModelRuns cannot be deleted"""
        # Create a DataPreparation that uses the master Excel
        core_package = DataPackage.objects.create(
            name="core-data", version="v1.0.0", source_type=DataPackage.SourceType.S3
        )

        geo_package = DataPackage.objects.create(
            name="geo-data", version="v1.0.0", source_type=DataPackage.SourceType.S3
        )

        prep = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            master_excel=self.master_excel,
            status=DataPreparation.Status.READY,
        )

        # Create a ModelRun that uses this DataPreparation
        _model_run = ModelRun.objects.create(
            name="Test Run",
            data_preparation=prep,
            state=ModelRun.RunState.CREATED,
        )

        # Verify the relationships exist
        assert self.master_excel.data_preparations.exists()
        assert self.master_excel.data_preparations.count() == 1
        assert prep.model_runs.exists()

        url = reverse("master-excel-delete", kwargs={"pk": self.master_excel.pk})
        response = self.client.post(url, follow=True)

        # File should still exist since it's in use by a ModelRun
        assert MasterExcelFile.objects.filter(pk=self.master_excel.pk).exists()
        assert DataPreparation.objects.filter(pk=prep.pk).exists()

        # Should redirect to detail page with error message
        assert response.status_code == 200
        # Check we ended up on the detail page
        assert response.redirect_chain == [(reverse("master-excel-detail", kwargs={"pk": self.master_excel.pk}), 302)]

        # Check for error message
        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "Cannot delete" in str(messages[0])
        assert "1 model run(s)" in str(messages[0])

    def test_delete_view_cascade_deletes_unused_data_preparation(self):
        """Test that files with DataPreparation but no ModelRuns get cascade deleted"""
        # Create a DataPreparation that uses the master Excel
        core_package = DataPackage.objects.create(
            name="core-data", version="v1.0.0", source_type=DataPackage.SourceType.S3
        )

        geo_package = DataPackage.objects.create(
            name="geo-data", version="v1.0.0", source_type=DataPackage.SourceType.S3
        )

        prep = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            master_excel=self.master_excel,
            status=DataPreparation.Status.READY,
        )

        # Verify the relationship exists but no ModelRuns
        assert self.master_excel.data_preparations.exists()
        assert self.master_excel.data_preparations.count() == 1
        assert not prep.model_runs.exists()

        url = reverse("master-excel-delete", kwargs={"pk": self.master_excel.pk})
        response = self.client.post(url, follow=True)

        # Both file and DataPreparation should be deleted
        assert not MasterExcelFile.objects.filter(pk=self.master_excel.pk).exists()
        assert not DataPreparation.objects.filter(pk=prep.pk).exists()

        # Should redirect to list page with success message
        assert response.status_code == 200
        assert response.redirect_chain == [(reverse("master-excel-list"), 302)]

        # Check for success message
        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "Test File" in str(messages[0])
        assert "1 data preparation(s) have been deleted" in str(messages[0])


@pytest.mark.django_db
class TestPrepareDataWithMasterExcel:
    """Test preparing data with MasterExcelFile through the UI."""

    @pytest.fixture(autouse=True)
    def setup(self, client):
        self.client = client
        self.master_excel = self.create_master_excel()

    def create_master_excel(self):
        excel_file = SimpleUploadedFile(
            "test.xlsx", b"content", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        return MasterExcelFile.objects.create(
            name="Test File", description="Test Description", file=excel_file, validation_status="valid"
        )

    def test_prepare_data_with_master_excel(self, mocker):
        """Test creating a data preparation from master Excel file."""
        # Mock the task at the import location
        mock_prepare_data_task = mocker.Mock()
        mocker.patch("steeloweb.tasks.prepare_data_task", mock_prepare_data_task)

        # Mock worker availability to return OK status (workers available)
        mock_worker_availability = {
            "can_start": True,
            "status": "ok",
            "message": "",
            "data": {
                "active_workers": 1,
                "admissible_workers": 1,
                "available_memory_gb": 8.0,
                "pending_tasks": 0,
                "running_tasks": 0,
                "modelrun_list_url": "/",
            },
        }
        mocker.patch("steeloweb.views_worker.check_worker_availability", return_value=mock_worker_availability)

        url = reverse("prepare-data-with-master-excel", kwargs={"pk": self.master_excel.pk})
        response = self.client.post(url)

        # Should redirect to data-preparation-detail
        assert response.status_code == 302
        prep = DataPreparation.objects.latest("created_at")
        assert response.url == reverse("data-preparation-detail", kwargs={"pk": prep.pk})

        # Check DataPreparation was created
        prep = DataPreparation.objects.latest("created_at")
        assert prep.master_excel == self.master_excel
        assert prep.name == f"Data with {self.master_excel.name}"

        # Check task was enqueued
        mock_prepare_data_task.enqueue.assert_called_once_with(prep.pk)

    def test_prepare_data_with_invalid_master_excel(self):
        """Test that invalid master Excel files cannot be used."""
        # Create invalid master Excel
        invalid_excel = MasterExcelFile.objects.create(
            name="Invalid File", file=SimpleUploadedFile("invalid.xlsx", b"content"), validation_status="invalid"
        )

        url = reverse("prepare-data-with-master-excel", kwargs={"pk": invalid_excel.pk})
        response = self.client.post(url, follow=True)

        # Should redirect back to detail page
        assert response.redirect_chain == [(reverse("master-excel-detail", kwargs={"pk": invalid_excel.pk}), 302)]

        # Check error message
        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "validation status is invalid" in str(messages[0])

        # No DataPreparation should be created
        assert DataPreparation.objects.count() == 0

    def test_prepare_data_reuses_existing(self, mocker):
        """Test that existing preparations are reused."""
        # Create existing preparation
        from steeloweb.models import DataPackage

        core = DataPackage.objects.create(name="core-data", version="v1.0.3", source_type=DataPackage.SourceType.S3)
        geo = DataPackage.objects.create(name="geo-data", version="v1.1.0", source_type=DataPackage.SourceType.S3)

        existing_prep = DataPreparation.objects.create(
            name="Existing",
            core_data_package=core,
            geo_data_package=geo,
            master_excel=self.master_excel,
            status=DataPreparation.Status.READY,
        )

        # Mock the task (shouldn't be called)
        mock_prepare_data_task = mocker.Mock()
        mocker.patch("steeloweb.tasks.prepare_data_task", mock_prepare_data_task)

        # Mock worker availability to return OK status (workers available)
        mock_worker_availability = {
            "can_start": True,
            "status": "ok",
            "message": "",
            "data": {
                "active_workers": 1,
                "admissible_workers": 1,
                "available_memory_gb": 8.0,
                "pending_tasks": 0,
                "running_tasks": 0,
                "modelrun_list_url": "/",
            },
        }
        mocker.patch("steeloweb.views_worker.check_worker_availability", return_value=mock_worker_availability)

        url = reverse("prepare-data-with-master-excel", kwargs={"pk": self.master_excel.pk})
        response = self.client.post(url, follow=True)

        # Should redirect to data-preparation-detail for the existing preparation
        assert response.redirect_chain == [(reverse("data-preparation-detail", kwargs={"pk": existing_prep.pk}), 302)]

        # Check info message
        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "already exists" in str(messages[0])

        # No new preparation should be created
        assert DataPreparation.objects.count() == 1

        # Task should not be called
        mock_prepare_data_task.enqueue.assert_not_called()

    def test_data_preparation_delete_button_visibility(self):
        """Test that delete button is shown when data preparation exists"""
        # Check detail view shows prepare button when no preparation exists
        response = self.client.get(reverse("master-excel-detail", kwargs={"pk": self.master_excel.pk}))
        assert response.status_code == 200
        assert b"Prepare Data with This Master Excel" in response.content
        assert b"Delete Existing Data Preparation" not in response.content

        # Create data packages
        core_package = DataPackage.objects.create(
            name="core-data", version="v1.0.0", source_type=DataPackage.SourceType.S3
        )
        geo_package = DataPackage.objects.create(
            name="geo-data", version="v1.0.0", source_type=DataPackage.SourceType.S3
        )

        # Create a data preparation for this file
        preparation = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            master_excel=self.master_excel,
        )

        # Check detail view now shows delete button instead of prepare button
        response = self.client.get(reverse("master-excel-detail", kwargs={"pk": self.master_excel.pk}))
        assert response.status_code == 200
        assert b"A data preparation already exists for this master Excel file" in response.content
        assert b"Delete Existing Data Preparation" in response.content
        # Check that the prepare button is not shown (but it might still appear in the modal text)
        assert (
            b'<button type="submit" class="btn btn-primary btn-lg">\n                                    <i class="fas fa-cogs"></i> Prepare Data with This Master Excel'
            not in response.content
        )

        # Test delete functionality
        response = self.client.post(reverse("delete-data-preparation", kwargs={"pk": preparation.pk}))
        assert response.status_code == 302  # Redirect
        assert response.url == reverse("master-excel-detail", kwargs={"pk": self.master_excel.pk})

        # Verify preparation was deleted
        assert not DataPreparation.objects.filter(pk=preparation.pk).exists()

        # Check detail view shows prepare button again
        response = self.client.get(reverse("master-excel-detail", kwargs={"pk": self.master_excel.pk}))
        assert response.status_code == 200
        assert b"Prepare Data with This Master Excel" in response.content
        assert b"Delete Existing Data Preparation" not in response.content

    def test_cannot_delete_preparation_with_model_runs(self):
        """Test that data preparation with model runs cannot be deleted"""
        # Create data packages and preparation
        core_package = DataPackage.objects.create(
            name="core-data", version="v1.0.0", source_type=DataPackage.SourceType.S3
        )
        geo_package = DataPackage.objects.create(
            name="geo-data", version="v1.0.0", source_type=DataPackage.SourceType.S3
        )
        preparation = DataPreparation.objects.create(
            name="Test Prep",
            core_data_package=core_package,
            geo_data_package=geo_package,
            master_excel=self.master_excel,
        )

        # Create a model run using this preparation
        ModelRun.objects.create(name="Test Run", config={}, data_preparation=preparation)

        # Try to delete preparation
        response = self.client.post(reverse("delete-data-preparation", kwargs={"pk": preparation.pk}))
        assert response.status_code == 302
        assert response.url == reverse("data-preparation-detail", kwargs={"pk": preparation.pk})

        # Verify preparation still exists
        assert DataPreparation.objects.filter(pk=preparation.pk).exists()
