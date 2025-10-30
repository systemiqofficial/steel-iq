import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from steeloweb.models import ModelRun, ResultImages, SimulationPlot


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
    """Test ModelRun CSV capture and ResultImages functionality."""

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
        output_dir = tmp_path / "TM"
        output_dir.mkdir(parents=True)

        result = modelrun.capture_result_csv(output_dir=output_dir)

        assert result is False
        assert not modelrun.result_csv

    def test_capture_result_csv_single_file(self, tmp_path, media_root):
        """Test CSV capture with a single CSV file."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.RUNNING)
        output_dir = tmp_path / "TM"
        output_dir.mkdir(parents=True)

        # Create a test CSV file
        csv_file = output_dir / "post_processed_2025-06-20 10-30.csv"
        csv_content = "year,location,technology,production\n2025,USA,BF-BOF,1000\n"
        csv_file.write_text(csv_content)

        result = modelrun.capture_result_csv(output_dir=output_dir.parent)

        assert result is True
        assert modelrun.result_csv
        assert modelrun.result_csv.read() == csv_content.encode()
        assert f"simulation_results_{modelrun.id}.csv" in modelrun.result_csv.name

    def test_capture_result_csv_multiple_files(self, tmp_path, media_root):
        """Test CSV capture selects the most recent file."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.RUNNING)
        output_dir = tmp_path / "TM"
        output_dir.mkdir(parents=True)

        # Create multiple CSV files with different timestamps
        old_file = output_dir / "post_processed_2025-06-19 10-30.csv"
        old_file.write_text("old data")
        old_file.touch()  # Ensure it exists

        # Wait a moment to ensure different mtime
        import time

        time.sleep(0.01)

        new_file = output_dir / "post_processed_2025-06-20 10-30.csv"
        new_content = "year,location,technology,production\n2025,USA,EAF,2000\n"
        new_file.write_text(new_content)

        result = modelrun.capture_result_csv(output_dir=output_dir.parent)

        assert result is True
        assert modelrun.result_csv
        assert modelrun.result_csv.read() == new_content.encode()

    def test_capture_result_csv_exception_handling(self, tmp_path, monkeypatch):
        """Test CSV capture handles exceptions gracefully."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.RUNNING)
        output_dir = tmp_path / "TM"
        output_dir.mkdir(parents=True)

        csv_file = output_dir / "post_processed_2025-06-20 10-30.csv"
        csv_file.write_text("test data")

        # Mock open to raise an exception
        def mock_open(*args, **kwargs):
            raise IOError("Cannot read file")

        monkeypatch.setattr("builtins.open", mock_open)

        result = modelrun.capture_result_csv(output_dir=output_dir.parent)

        assert result is False


@pytest.mark.django_db
class TestResultImages:
    """Test ResultImages creation and plot detection."""

    def test_create_from_plots_with_real_plots(self, tmp_path):
        """Test creating ResultImages when real plots exist."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create plot directories
        geo_plots_dir = tmp_path / "GEO"
        pam_plots_dir = tmp_path / "PAM"
        geo_plots_dir.mkdir(parents=True)
        pam_plots_dir.mkdir(parents=True)

        # Create test plot files
        lcoe_plot = geo_plots_dir / "lcoe_map.png"
        lcoe_plot.write_bytes(b"fake lcoe image data")

        lcoh_plot = geo_plots_dir / "lcoh_map.png"
        lcoh_plot.write_bytes(b"fake lcoh image data")

        iron_plot = geo_plots_dir / "top5_priority_locations_iron.png"
        iron_plot.write_bytes(b"fake iron image data")

        steel_plot = geo_plots_dir / "top5_priority_locations_steel.png"
        steel_plot.write_bytes(b"fake steel image data")

        # Create ResultImages
        result_images = ResultImages.create_from_plots(modelrun, plots_dir=tmp_path)

        assert result_images.modelrun == modelrun
        assert result_images.lcoe_map
        assert result_images.lcoh_map
        assert result_images.priority_locations_iron
        assert result_images.priority_locations_steel

        # Verify the correct files were used
        assert "lcoe_map.png" in result_images.lcoe_map.name
        assert "lcoh_map.png" in result_images.lcoh_map.name
        assert "top5_priority_locations_iron.png" in result_images.priority_locations_iron.name
        assert "top5_priority_locations_steel.png" in result_images.priority_locations_steel.name

    def test_create_from_plots_no_images_available(self, tmp_path):
        """Test creating ResultImages when no images are available - fields remain empty."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create empty output directory
        empty_output = tmp_path / "empty_output"
        empty_output.mkdir()

        result_images = ResultImages.create_from_plots(modelrun, plots_dir=empty_output)

        assert result_images.modelrun == modelrun
        # Images should not be set if files don't exist (no demo fallback)
        assert not result_images.lcoe_map
        assert not result_images.lcoh_map
        assert not result_images.priority_locations_iron
        assert not result_images.priority_locations_steel

    def test_create_from_plots_with_different_priority_percentages(self, tmp_path):
        """Test creating ResultImages with different priority percentages (e.g., top20 instead of top5)."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create plot directories
        geo_plots_dir = tmp_path / "GEO"
        geo_plots_dir.mkdir(parents=True)

        # Create test plot files with 20% priority (top20)
        lcoe_plot = geo_plots_dir / "optimal_lcoe_2025.png"
        lcoe_plot.write_bytes(b"fake lcoe image with year")

        lcoh_plot = geo_plots_dir / "optimal_lcoh_2025.png"
        lcoh_plot.write_bytes(b"fake lcoh image with year")

        iron_plot = geo_plots_dir / "top20_priority_locations_iron_2025.png"
        iron_plot.write_bytes(b"fake iron 20% image")

        steel_plot = geo_plots_dir / "top20_priority_locations_steel_2025.png"
        steel_plot.write_bytes(b"fake steel 20% image")

        # Create ResultImages
        result_images = ResultImages.create_from_plots(modelrun, plots_dir=tmp_path)

        assert result_images.modelrun == modelrun
        assert result_images.lcoe_map
        assert result_images.lcoh_map
        assert result_images.priority_locations_iron
        assert result_images.priority_locations_steel

        # Verify the glob patterns matched the top20 files
        assert "optimal_lcoe_2025.png" in result_images.lcoe_map.name
        assert "optimal_lcoh_2025.png" in result_images.lcoh_map.name
        assert "top20_priority_locations_iron_2025.png" in result_images.priority_locations_iron.name
        assert "top20_priority_locations_steel_2025.png" in result_images.priority_locations_steel.name

    def test_create_from_plots_prefers_year_specific_files(self, tmp_path):
        """Test that year-specific files are preferred over generic ones."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create plot directories
        geo_plots_dir = tmp_path / "GEO"
        geo_plots_dir.mkdir(parents=True)

        # Create both year-specific and generic priority location files
        # Year-specific should be preferred
        iron_plot_2025 = geo_plots_dir / "top15_priority_locations_iron_2025.png"
        iron_plot_2025.write_bytes(b"iron 2025 specific")

        iron_plot_generic = geo_plots_dir / "top15_priority_locations_iron.png"
        iron_plot_generic.write_bytes(b"iron generic")

        steel_plot_2025 = geo_plots_dir / "top15_priority_locations_steel_2025.png"
        steel_plot_2025.write_bytes(b"steel 2025 specific")

        # Create ResultImages
        result_images = ResultImages.create_from_plots(modelrun, plots_dir=tmp_path)

        assert result_images.modelrun == modelrun
        assert result_images.priority_locations_iron
        assert result_images.priority_locations_steel

        # Verify year-specific files were preferred
        assert "top15_priority_locations_iron_2025.png" in result_images.priority_locations_iron.name
        assert "top15_priority_locations_steel_2025.png" in result_images.priority_locations_steel.name

    def test_create_from_plots_sorts_by_year_not_filename(self, tmp_path):
        """Test that plots are sorted by year, not lexicographically by filename."""
        modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create plot directories
        geo_plots_dir = tmp_path / "GEO"
        geo_plots_dir.mkdir(parents=True)

        # Create plots with different years and percentages
        # The key test: top5_2027 should NOT be preferred over top20_2035
        # even though '5' > '2' lexicographically
        import time

        iron_plot_old = geo_plots_dir / "top5_priority_locations_iron_2027.png"
        iron_plot_old.write_bytes(b"old iron 2027 with 5%")
        time.sleep(0.01)  # Ensure different mtime

        iron_plot_new = geo_plots_dir / "top20_priority_locations_iron_2035.png"
        iron_plot_new.write_bytes(b"new iron 2035 with 20%")

        steel_plot_old = geo_plots_dir / "top10_priority_locations_steel_2030.png"
        steel_plot_old.write_bytes(b"old steel 2030")
        time.sleep(0.01)

        steel_plot_new = geo_plots_dir / "top25_priority_locations_steel_2040.png"
        steel_plot_new.write_bytes(b"new steel 2040")

        # Also test LCOE/LCOH year sorting
        lcoe_old = geo_plots_dir / "optimal_lcoe_2025.png"
        lcoe_old.write_bytes(b"lcoe 2025")
        time.sleep(0.01)

        lcoe_new = geo_plots_dir / "optimal_lcoe_2035.png"
        lcoe_new.write_bytes(b"lcoe 2035")

        # Create ResultImages
        result_images = ResultImages.create_from_plots(modelrun, plots_dir=tmp_path)

        assert result_images.modelrun == modelrun

        # Verify the NEWEST year is selected, regardless of percentage
        assert "top20_priority_locations_iron_2035.png" in result_images.priority_locations_iron.name
        assert "top25_priority_locations_steel_2040.png" in result_images.priority_locations_steel.name
        assert "optimal_lcoe_2035.png" in result_images.lcoe_map.name

        # Verify the content matches the newest plots
        assert result_images.priority_locations_iron.read() == b"new iron 2035 with 20%"
        assert result_images.priority_locations_steel.read() == b"new steel 2040"
        assert result_images.lcoe_map.read() == b"lcoe 2035"


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

        # Create plot directory structure
        output_dir = tmp_path
        pam_plots_dir = output_dir / "plots" / "PAM"
        pam_plots_dir.mkdir(parents=True)

        # Create test plot files
        plot_files = [
            "year2year_added_capacity_by_technology.png",
            "Capacity_development_by_technology.png",
            "steel_cost_curve_2030.png",
            "iron_cost_curve_by_region_2030.png",
            "iron_cost_curve_by_technology_2030.png",
            "steel_production_development_by_region.png",
            "iron_production_development_by_region.png",
            "steel_production_development_by_technology.png",
            "iron_production_development_by_technology.png",
        ]

        for plot_file in plot_files:
            with open(pam_plots_dir / plot_file, "wb") as f:
                f.write(b"fake png content")

        # Capture plots
        plots = SimulationPlot.capture_simulation_plots(modelrun, pam_plots_dir=pam_plots_dir)

        assert len(plots) == 9
        assert modelrun.simulation_plots.count() == 9

        # Check plot types
        plot_types = modelrun.simulation_plots.values_list("plot_type", flat=True)
        assert SimulationPlot.PlotType.CAPACITY_ADDED in plot_types
        assert SimulationPlot.PlotType.CAPACITY_DEVELOPMENT in plot_types
        assert SimulationPlot.PlotType.COST_CURVE in plot_types
        assert SimulationPlot.PlotType.PRODUCTION_REGION in plot_types
        assert SimulationPlot.PlotType.PRODUCTION_TECHNOLOGY in plot_types

        # Check product types
        steel_plots = modelrun.simulation_plots.filter(product_type="steel")
        iron_plots = modelrun.simulation_plots.filter(product_type="iron")
        assert steel_plots.count() == 3  # cost curve, production by region, production by technology
        assert iron_plots.count() == 4  # cost curves (x2), production by region, production by technology

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
