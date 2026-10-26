import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from steeloweb.models import ModelRun, SimulationPlot


@pytest.fixture
def media_root(tmp_path, settings):
    """Create a temporary media root for tests."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    # Also create the results/csv subdirectory
    results_dir = media_dir / "results" / "csv"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Use pytest-django's settings fixture to override MEDIA_ROOT
    settings.MEDIA_ROOT = str(media_dir)

    yield media_dir


@pytest.mark.django_db
class TestModelRunVisualization:
    """Test ModelRun CSV capture."""

    def test_capture_result_csv_no_output_dir(self, tmp_path):
        """Test CSV capture when output directory doesn't exist."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.RUNNING)
        non_existent_dir = tmp_path / "non_existent"

        result = modelrun.capture_result_csv(output_dir=non_existent_dir)

        assert result is False
        assert not modelrun.result_csv

    def test_capture_result_csv_no_csv_files(self, tmp_path):
        """Test CSV capture when no CSV files exist."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.RUNNING)

        result = modelrun.capture_result_csv(output_dir=tmp_path)

        assert result is False
        assert not modelrun.result_csv

    def test_capture_result_csv_single_file(self, tmp_path, media_root):
        """Test CSV capture with a single CSV file."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.RUNNING)

        csv_file = tmp_path / "post_processed_2025-06-20_10-30.csv"
        csv_content = "year,location,technology,production\n2025,USA,BF-BOF,1000\n"
        csv_file.write_text(csv_content)

        result = modelrun.capture_result_csv(output_dir=tmp_path)

        assert result is True
        assert modelrun.result_csv
        assert modelrun.result_csv.read() == csv_content.encode()
        assert f"simulation_results_{modelrun.id}.csv" in modelrun.result_csv.name

    def test_capture_result_csv_multiple_files(self, tmp_path, media_root):
        """Test CSV capture selects the most recent file."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.RUNNING)

        old_file = tmp_path / "post_processed_2025-06-19_10-30.csv"
        old_file.write_text("old data")
        old_file.touch()

        import time

        time.sleep(0.01)

        new_file = tmp_path / "post_processed_2025-06-20_10-30.csv"
        new_content = "year,location,technology,production\n2025,USA,EAF,2000\n"
        new_file.write_text(new_content)

        result = modelrun.capture_result_csv(output_dir=tmp_path)

        assert result is True
        assert modelrun.result_csv
        assert modelrun.result_csv.read() == new_content.encode()

    def test_capture_result_csv_exception_handling(self, tmp_path, monkeypatch):
        """Test CSV capture handles exceptions gracefully."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.RUNNING)

        csv_file = tmp_path / "post_processed_2025-06-20_10-30.csv"
        csv_file.write_text("test data")

        def mock_open(*args, **kwargs):
            raise IOError("Cannot read file")

        monkeypatch.setattr("builtins.open", mock_open)

        result = modelrun.capture_result_csv(output_dir=tmp_path)

        assert result is False


@pytest.mark.django_db
class TestDownloadModelRunCSV:
    """Test the download CSV view functionality."""

    def test_download_csv_success(self, client):
        """Test successful CSV download."""
        # Create a ModelRun with CSV results
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)
        csv_content = b"year,location,technology,production\n2025,USA,EAF,1000\n"
        modelrun.result_csv.save("test_results.csv", ContentFile(csv_content))

        url = reverse("download-modelrun-csv", kwargs={"pk": modelrun.pk})
        response = client.get(url)

        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert response["Content-Disposition"] == f'attachment; filename="modelrun_{modelrun.id}_results.csv"'
        assert response.content == csv_content

    def test_download_csv_no_results(self, client):
        """Test download when no CSV results exist."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        url = reverse("download-modelrun-csv", kwargs={"pk": modelrun.pk})
        response = client.get(url)

        assert response.status_code == 404

    def test_download_csv_nonexistent_modelrun(self, client):
        """Test download with non-existent ModelRun ID."""
        url = reverse("download-modelrun-csv", kwargs={"pk": 99999})
        response = client.get(url)

        assert response.status_code == 404

    def test_download_csv_file_read_error(self, client, monkeypatch):
        """Test handling of file read errors."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)
        modelrun.result_csv.save("test_results.csv", ContentFile(b"test data"))
        modelrun.save()

        # Mock the file field's read method at the storage level
        def mock_read(self):
            raise IOError("Cannot read file")

        # Patch the read method on the FieldFile class
        from django.db.models.fields.files import FieldFile

        monkeypatch.setattr(FieldFile, "read", mock_read)

        url = reverse("download-modelrun-csv", kwargs={"pk": modelrun.pk})
        response = client.get(url, follow=True)

        # Should redirect to modelrun detail page with error message
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse("modelrun-detail", kwargs={"pk": modelrun.pk})

        # Check for error message in messages framework
        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "Error downloading CSV" in str(messages[0])


@pytest.mark.django_db
class TestSimulationPlot:
    """Test SimulationPlot functionality."""

    def test_capture_simulation_plots_no_dir(self, tmp_path):
        """Test plot capture when plots directory doesn't exist."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)
        pam_plots_dir = tmp_path / "nonexistent"

        plots = SimulationPlot.capture_simulation_plots(modelrun, pam_plots_dir=pam_plots_dir)

        assert plots == []
        assert modelrun.simulation_plots.count() == 0

    def test_capture_simulation_plots_with_plots(self, tmp_path, media_root):
        """Test plot capture with actual plot files."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create plot directory structure: cost curves and emissions live in sibling
        # folders to PAM (plots/cost_curves, plots/emissions).
        output_dir = tmp_path
        pam_plots_dir = output_dir / "plots" / "PAM"
        cost_curves_dir = output_dir / "plots" / "cost_curves"
        pam_plots_dir.mkdir(parents=True)
        cost_curves_dir.mkdir(parents=True)

        pam_files = [
            "steel_production_development_by_region.png",
            "iron_production_development_by_region.png",
            "steel_production_development_by_technology.png",
            "iron_production_development_by_technology.png",
            "steel_capacity_development_by_technology.png",
            "iron_capacity_development_by_technology.png",
        ]
        cost_curve_files = [
            "cost_curve_steel_by_region_2030.png",
            "cost_curve_iron_by_region_2030.png",
            "cost_curve_iron_by_technology_2030.png",
        ]

        for filename in pam_files:
            (pam_plots_dir / filename).write_bytes(b"fake png content")
        for filename in cost_curve_files:
            (cost_curves_dir / filename).write_bytes(b"fake png content")

        # Capture plots
        plots = SimulationPlot.capture_simulation_plots(modelrun, pam_plots_dir=pam_plots_dir)

        assert len(plots) == 9
        assert modelrun.simulation_plots.count() == 9

        # Check plot types
        plot_types = modelrun.simulation_plots.values_list("plot_type", flat=True)
        assert SimulationPlot.PlotType.COST_CURVE in plot_types
        assert SimulationPlot.PlotType.PRODUCTION_REGION in plot_types
        assert SimulationPlot.PlotType.PRODUCTION_TECHNOLOGY in plot_types
        assert SimulationPlot.PlotType.CAPACITY_TECHNOLOGY in plot_types

        # Check product types
        steel_plots = modelrun.simulation_plots.filter(product_type="steel")
        iron_plots = modelrun.simulation_plots.filter(product_type="iron")
        assert steel_plots.count() == 4  # cost curve, production by region/tech, capacity by tech
        assert iron_plots.count() == 5  # cost curves (x2), production by region/tech, capacity by tech

        # Ensure cost curves keep steel plots ahead of iron plots
        cost_curve_products = list(
            modelrun.simulation_plots.filter(plot_type=SimulationPlot.PlotType.COST_CURVE).values_list(
                "product_type", flat=True
            )
        )
        assert cost_curve_products  # sanity
        last_steel = max(idx for idx, product in enumerate(cost_curve_products) if product == "steel")
        first_iron = min(idx for idx, product in enumerate(cost_curve_products) if product == "iron")
        assert last_steel < first_iron

    def test_capture_production_by_technology_plots(self, tmp_path, media_root):
        """Test that production by technology plots are captured correctly."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create plot directory
        pam_plots_dir = tmp_path / "plots" / "PAM"
        pam_plots_dir.mkdir(parents=True)

        # Create production by technology plot files
        steel_plot = pam_plots_dir / "steel_production_development_by_technology.png"
        iron_plot = pam_plots_dir / "iron_production_development_by_technology.png"

        steel_plot.write_bytes(b"fake steel tech plot")
        iron_plot.write_bytes(b"fake iron tech plot")

        # Capture plots
        plots = SimulationPlot.capture_simulation_plots(modelrun, pam_plots_dir=pam_plots_dir)

        # Verify both plots were captured
        tech_plots = [p for p in plots if p.plot_type == SimulationPlot.PlotType.PRODUCTION_TECHNOLOGY]
        assert len(tech_plots) == 2

        # Verify product types
        product_types = {p.product_type for p in tech_plots}
        assert product_types == {"steel", "iron"}

        # Verify titles
        titles = {p.title for p in tech_plots}
        assert titles == {"Steel Production by Technology", "Iron Production by Technology"}

        # Verify images were saved
        for plot in tech_plots:
            assert plot.image
            assert plot.image.name
            if plot.product_type == "steel":
                assert plot.image.read() == b"fake steel tech plot"
            else:
                assert plot.image.read() == b"fake iron tech plot"

    def test_simulation_plot_model_str(self):
        """Test SimulationPlot string representation."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)
        plot = SimulationPlot.objects.create(
            modelrun=modelrun,
            plot_type=SimulationPlot.PlotType.CAPACITY_ADDED,
            title="Test Plot",
            image=ContentFile(b"fake content", name="test.png"),
        )

        assert str(plot) == f"Test Plot - {modelrun.id}"

    def test_view_simulation_plot(self, client):
        """Test viewing a simulation plot in full size."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)
        plot = SimulationPlot.objects.create(
            modelrun=modelrun,
            plot_type=SimulationPlot.PlotType.CAPACITY_ADDED,
            title="Test Plot",
            image=ContentFile(b"fake content", name="test.png"),
        )

        url = reverse("view-simulation-plot", kwargs={"pk": modelrun.id, "plot_id": plot.id})
        response = client.get(url)

        assert response.status_code == 200
        assert "Test Plot" in response.content.decode()
        assert f"Model Run #{modelrun.id}" in response.content.decode()
