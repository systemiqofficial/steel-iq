"""Tests for enhanced technology validation."""

import pytest
from steelo.validation import check_product_coverage_enhanced
from steelo.simulation_types import TechnologySettings


class TestEnhancedValidation:
    """Test the enhanced validation functionality."""

    def test_check_product_coverage_enhanced_with_missing_iron(self):
        """Test validation when iron technologies are missing."""
        # Setup: Only steel technologies enabled
        technology_settings = {
            "BF": TechnologySettings(allowed=False, from_year=2025),
            "DRI": TechnologySettings(allowed=False, from_year=2025),
            "EAF": TechnologySettings(allowed=True, from_year=2025),
            "BOF": TechnologySettings(allowed=True, from_year=2025),
        }

        tech_to_product = {
            "BF": "iron",
            "DRI": "iron",
            "EAF": "steel",
            "BOF": "steel",
        }

        technologies_data = {
            "bf": {"display_name": "Blast Furnace", "normalized_code": "BF"},
            "dri": {"display_name": "Direct Reduced Iron", "normalized_code": "DRI"},
            "eaf": {"display_name": "Electric Arc Furnace", "normalized_code": "EAF"},
            "bof": {"display_name": "Basic Oxygen Furnace", "normalized_code": "BOF"},
        }

        result = check_product_coverage_enhanced(technology_settings, tech_to_product, technologies_data, 2025, 2030)

        # Should fail validation with missing iron error
        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].title == "Missing Iron Production"
        assert "iron production technologies are enabled" in result.errors[0].description
        assert result.errors[0].product_type == "iron"
        assert len(result.errors[0].suggestions) == 2  # BF and DRI

    def test_check_product_coverage_enhanced_with_valid_config(self):
        """Test validation with valid technology configuration."""
        # Setup: Both iron and steel technologies enabled
        technology_settings = {
            "BF": TechnologySettings(allowed=True, from_year=2025),
            "EAF": TechnologySettings(allowed=True, from_year=2025),
        }

        tech_to_product = {
            "BF": "iron",
            "EAF": "steel",
        }

        technologies_data = {
            "bf": {"display_name": "Blast Furnace", "normalized_code": "BF"},
            "eaf": {"display_name": "Electric Arc Furnace", "normalized_code": "EAF"},
        }

        result = check_product_coverage_enhanced(technology_settings, tech_to_product, technologies_data, 2025, 2030)

        # Should pass validation
        assert result.is_valid
        assert len(result.errors) == 0

    def test_dri_technology_mapping(self):
        """Test that DRI technology is correctly mapped to iron."""
        technology_settings = {
            "DRI": TechnologySettings(allowed=True, from_year=2025),
            "EAF": TechnologySettings(allowed=True, from_year=2025),
        }

        tech_to_product = {
            "DRI": "iron",
            "EAF": "steel",
        }

        technologies_data = {
            "dri": {"display_name": "Direct Reduced Iron", "normalized_code": "DRI"},
            "eaf": {"display_name": "Electric Arc Furnace", "normalized_code": "EAF"},
        }

        result = check_product_coverage_enhanced(technology_settings, tech_to_product, technologies_data, 2025, 2030)

        # Should pass validation - DRI provides iron coverage
        assert result.is_valid
        assert len(result.errors) == 0


@pytest.mark.django_db
class TestEnhancedValidationIntegration:
    """Integration tests for enhanced validation in views."""

    def test_template_receives_validation_errors(self):
        """Test that validation errors are properly passed to template context."""
        from steeloweb.views import _extract_technology_settings
        from steeloweb.models import DataPreparation
        from steeloweb.forms import ModelRunCreateForm
        from unittest.mock import Mock

        # Create a mock data preparation
        mock_prep = Mock(spec=DataPreparation)
        mock_prep.get_technologies.return_value = {
            "bf": {"display_name": "Blast Furnace", "normalized_code": "BF", "product_type": "iron"},
            "eaf": {"display_name": "Electric Arc Furnace", "normalized_code": "EAF", "product_type": "steel"},
            "bof": {"display_name": "Basic Oxygen Furnace", "normalized_code": "BOF", "product_type": "steel"},
            "dri": {"display_name": "Direct Reduced Iron", "normalized_code": "DRI", "product_type": "iron"},
        }

        # Create a mock request with POST data (only steel technologies enabled - missing iron)
        mock_request = Mock()
        mock_request.POST = {
            "tech_bf_allowed": "",  # unchecked = disabled
            "tech_bf_from_year": "2025",
            "tech_eaf_allowed": "on",  # checked = enabled
            "tech_eaf_from_year": "2025",
            "tech_bof_allowed": "on",  # checked = enabled
            "tech_bof_from_year": "2025",
            "tech_dri_allowed": "",  # unchecked = disabled
            "tech_dri_from_year": "2025",
        }

        # Create a mock form with valid cleaned data
        mock_form = Mock(spec=ModelRunCreateForm)
        mock_form.cleaned_data = {"start_year": 2025, "end_year": 2030}

        # Test the extraction function
        result = _extract_technology_settings(mock_request, mock_prep, mock_form)

        # Should return empty dict (validation failed)
        assert result == {}

        # Should have validation errors attached to form
        assert hasattr(mock_form, "technology_validation_errors")
        assert len(mock_form.technology_validation_errors) == 1  # Missing iron

        # Check error structure
        errors = mock_form.technology_validation_errors
        assert errors[0].title == "Missing Iron Production"
        assert errors[0].product_type == "iron"
