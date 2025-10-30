"""Integration tests for total steel demand scenario form field."""

from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from steeloweb.models import ModelRun


class TestTotalSteelDemandForm(TestCase):
    """Test that total steel demand scenario field works in the form."""

    def test_form_includes_total_steel_demand_field(self):
        """Test that the create form includes total_steel_demand_scenario field."""
        response = self.client.get(reverse("create-modelrun"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "total_steel_demand_scenario")
        self.assertContains(response, "Select total steel demand scenario")

        # Check that field is disabled (has field-not-connected class)
        self.assertContains(response, 'id="id_total_steel_demand_scenario"')
        self.assertContains(response, 'class="form-select field-not-connected" disabled')

        # Check that green_steel_demand_scenario is also disabled
        self.assertContains(response, 'id="id_green_steel_demand_scenario"')
        self.assertContains(response, 'class="form-select field-not-connected" disabled')

    @patch("steeloweb.views._extract_technology_settings")
    def test_form_submission_saves_total_steel_demand(self, mock_extract):
        """Test that form submission saves total_steel_demand_scenario even though it's disabled."""
        # Setup mock to return valid technology settings
        mock_extract.return_value = {
            "BF": {"allowed": True, "from_year": 2025, "to_year": None},
            "BOF": {"allowed": True, "from_year": 2025, "to_year": None},
            "EAF": {"allowed": True, "from_year": 2025, "to_year": None},
            "DRING": {"allowed": True, "from_year": 2025, "to_year": None},
            "DRIH2EAF": {"allowed": True, "from_year": 2025, "to_year": None},
            "ESF": {"allowed": False, "from_year": 2025, "to_year": None},
            "MOE": {"allowed": False, "from_year": 2025, "to_year": None},
            "DRI": {"allowed": True, "from_year": 2025, "to_year": None},
            "DRIH2": {"allowed": True, "from_year": 2025, "to_year": None},
            "BFBOF": {"allowed": True, "from_year": 2025, "to_year": None},
            "DRINGEAF": {"allowed": True, "from_year": 2025, "to_year": None},
            "ESFEAF": {"allowed": False, "from_year": 2025, "to_year": None},
        }

        # Create a DataPreparation for the test
        from steeloweb.models import DataPreparation, DataPackage

        # Create required data packages
        core_package, _ = DataPackage.objects.get_or_create(
            name=DataPackage.PackageType.CORE_DATA,
            defaults={
                "version": "1.0.0",
                "source_type": DataPackage.SourceType.LOCAL,
                "source_url": "",
            },
        )
        geo_package, _ = DataPackage.objects.get_or_create(
            name=DataPackage.PackageType.GEO_DATA,
            defaults={
                "version": "1.0.0",
                "source_type": DataPackage.SourceType.LOCAL,
                "source_url": "",
            },
        )

        data_preparation = DataPreparation.objects.create(
            name="Test Data Preparation",
            status=DataPreparation.Status.READY,
            core_data_package=core_package,
            geo_data_package=geo_package,
        )

        form_data = {
            "data_preparation": data_preparation.id,
            "start_year": 2025,
            "plant_lifetime": 20,
            "end_year": 2030,
            "total_steel_demand_scenario": "climate_neutrality",
            "green_steel_demand_scenario": "business_as_usual",
            "scrap_generation_scenario": "business_as_usual",
        }
        # Add dynamic technology fields inline
        for tech in ["BF", "BOF", "EAF", "DRING", "DRIH2EAF", "ESF", "MOE"]:
            form_data[f"tech_{tech}_allowed"] = "true"
            form_data[f"tech_{tech}_from_year"] = "2025"
            form_data[f"tech_{tech}_to_year"] = ""

        response = self.client.post(reverse("create-modelrun"), data=form_data)

        # Should redirect to the detail view
        self.assertEqual(response.status_code, 302)

        # Check that model was created
        modelrun = ModelRun.objects.latest("id")
        # The field is saved in config but won't be used by SimulationRunner
        self.assertEqual(modelrun.config["total_steel_demand_scenario"], "climate_neutrality")
        self.assertEqual(modelrun.config["start_year"], 2025)
        self.assertEqual(modelrun.config["end_year"], 2030)

    def test_scenario_choices_available(self):
        """Test that all scenario choices are available in the form."""
        response = self.client.get(reverse("create-modelrun"))

        # Check for scenario options
        scenario_choices = [
            "Business As Usual",
            "System Change",
            "Accelerated Transition",
            "Climate Neutrality 2050",
            "High Efficiency",
            "Circular Economy Focus",
            "Technology Breakthrough",
        ]

        for choice in scenario_choices:
            self.assertContains(response, choice)
