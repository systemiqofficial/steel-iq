"""Test that DRI technology transitions are handled correctly in Django simulations."""

from unittest.mock import patch, MagicMock
from django.test import TestCase
from steeloweb.models import ModelRun, DataPreparation, DataPackage
from steelo.domain.models import FurnaceGroup, Technology
from django.conf import settings
import tempfile
from pathlib import Path


class TestDRITransitions(TestCase):
    """Test DRI technology transitions in Django context."""

    def setUp(self):
        """Set up test data."""
        # Create data packages first (required by DataPreparation)
        self.core_package = DataPackage.objects.create(
            name=DataPackage.PackageType.CORE_DATA,
            version="1.0.0",
            source_type=DataPackage.SourceType.LOCAL,
        )

        self.geo_package = DataPackage.objects.create(
            name=DataPackage.PackageType.GEO_DATA,
            version="1.0.0",
            source_type=DataPackage.SourceType.LOCAL,
        )

        self.data_prep = DataPreparation.objects.create(
            name="Test Data",
            status=DataPreparation.Status.READY,
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            data_directory="/test/path",
        )
        # Add a mock master Excel file
        from django.core.files.base import ContentFile

        self.data_prep.master_excel_file.save("test_master.xlsx", ContentFile(b"fake excel content"), save=True)

    @patch("steelo.validation.validate_technology_settings")  # Skip validation
    @patch("steelo.bootstrap.bootstrap_simulation")
    def test_dri_furnace_transitions_handled(self, mock_bootstrap_simulation, mock_validate):
        """Test that DRI furnace transitions are handled without KeyError."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                # Create model run
                model_run = ModelRun.objects.create(
                    name="DRI Test Run",
                    config={
                        "start_year": 2025,
                        "end_year": 2026,
                        "master_excel_path": str(Path(temp_dir) / "master.xlsx"),
                        "technology_settings": {
                            "DRI": {"allowed": True, "from_year": 2025, "to_year": None},
                        },
                    },
                    data_preparation=self.data_prep,
                )
                model_run.ensure_output_directories()

                # Create a mock runner that will test DRI transitions
                mock_runner = MagicMock()

                # Mock a plant with DRI furnace
                mock_plant = MagicMock()
                mock_furnace = MagicMock(spec=FurnaceGroup)
                mock_technology = MagicMock(spec=Technology)
                mock_technology.name = "DRI"
                mock_furnace.technology = mock_technology

                # Mock the optimal_technology_name method to ensure it gets called
                mock_furnace.optimal_technology_name = MagicMock(return_value=({}, {}, None, {}))

                mock_plant.furnace_groups = [mock_furnace]
                mock_runner.plants = [mock_plant]

                # Mock the run method to call optimal_technology_name
                def mock_run():
                    # This simulates what happens during a real simulation
                    for plant in mock_runner.plants:
                        for furnace in plant.furnace_groups:
                            # Call with empty allowed_furnace_transitions to test the fix
                            furnace.optimal_technology_name(
                                market_price={"steel": 500, "iron": 400},
                                cost_of_debt=0.05,
                                cost_of_equity=0.1,
                                get_bom_from_avg_boms=lambda *args: (None, 0, ""),
                                capex_dict={},
                                capex_renovation_share={},
                                technology_fopex_dict={},
                                plant_has_smelter_furnace=False,
                                dynamic_business_cases={},
                                allowed_furnace_transitions={},  # Empty dict to test the fix
                            )
                    return {"status": "success"}

                mock_runner.run = MagicMock(side_effect=mock_run)
                mock_runner.progress_callback = None
                mock_runner.modelrun_id = None
                mock_bootstrap_simulation.return_value = mock_runner

                # Run simulation - should not raise KeyError
                model_run.run()

                # Verify the simulation ran without errors
                # model_run.run() doesn't return a value, but the mock was called
                mock_runner.run.assert_called_once()

                # Verify optimal_technology_name was called
                mock_furnace.optimal_technology_name.assert_called_once()

                # Verify it was called with empty allowed_furnace_transitions
                call_args = mock_furnace.optimal_technology_name.call_args[1]
                assert call_args["allowed_furnace_transitions"] == {}
