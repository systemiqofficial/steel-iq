"""Test that policy settings fields are properly configured and passed through."""

import pytest
from django.test import TestCase
from steeloweb.forms import ModelRunCreateForm


class TestPolicySettingsFields(TestCase):
    """Test policy settings fields in Django form."""

    def test_use_iron_ore_premiums_field_exists(self):
        """Test that use_iron_ore_premiums field exists with correct defaults."""
        form = ModelRunCreateForm()

        # Check field exists
        self.assertIn("use_iron_ore_premiums", form.fields)

        # Check it's a BooleanField
        field = form.fields["use_iron_ore_premiums"]
        self.assertEqual(field.__class__.__name__, "BooleanField")

        # Check default value
        self.assertEqual(field.initial, True)

    def test_include_tariffs_field_exists(self):
        """Test that include_tariffs field exists with correct defaults."""
        form = ModelRunCreateForm()

        # Check field exists
        self.assertIn("include_tariffs", form.fields)

        # Check it's a BooleanField
        field = form.fields["include_tariffs"]
        self.assertEqual(field.__class__.__name__, "BooleanField")

        # Check default value
        self.assertEqual(field.initial, True)

    @pytest.mark.django_db
    def test_policy_fields_in_form_submission(self):
        """Test that policy fields are included in form submission."""
        from steeloweb.models import DataPreparation, DataPackage

        # Create required data packages and preparation for the test
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

        data_prep = DataPreparation.objects.create(
            name="Test Data Preparation",
            status=DataPreparation.Status.READY,
            core_data_package=core_package,
            geo_data_package=geo_package,
        )

        form_data = {
            "start_year": 2025,
            "end_year": 2050,
            "plant_lifetime": 20,  # Required field
            "data_preparation": data_prep.id,  # Required field
            "use_iron_ore_premiums": False,
            "include_tariffs": False,
            # Add other required fields
            "total_steel_demand_scenario": "business_as_usual",
            "green_steel_demand_scenario": "business_as_usual",
            "scrap_generation_scenario": "business_as_usual",
        }

        form = ModelRunCreateForm(data=form_data)

        # Form should be valid with these fields
        if not form.is_valid():
            print(f"Form errors: {form.errors}")

        self.assertTrue(form.is_valid())

        # Check cleaned data contains our values
        self.assertEqual(form.cleaned_data["use_iron_ore_premiums"], False)
        self.assertEqual(form.cleaned_data["include_tariffs"], False)
