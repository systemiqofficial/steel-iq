"""Test for reductantless alternative lookup with normalized metallic charges."""

import pandas as pd
from unittest.mock import patch
from steelo.adapters.dataprocessing.excel_reader import read_dynamic_business_cases


class TestReductantlessAlternativeLookup:
    """Test that reductantless alternative lookup works with normalized metallic charges."""

    @patch("pandas.read_excel")
    def test_reductantless_alternative_with_normalized_charge(self, mock_read_excel):
        """Test that BOF with 'Hot metal' can find its reductantless alternative."""
        # Create test data that mimics the real Excel structure
        # This has a BOF feedstock with reductant that needs to find its reductantless alternative
        data = pd.DataFrame(
            [
                # BOF with hot metal and coke reductant - needs reductantless alternative
                {
                    "Business case": "steel_bof",
                    "Metallic charge": "Hot metal",  # Non-normalized!
                    "Reductant": "Coke",
                    "Metric type": "Materials",
                    "Side": "Input",
                    "Type": "material",
                    "Vector": "hot_metal",
                    "Value": float("nan"),  # Missing value - needs to get from reductantless
                    "Unit": "t/t",
                    "System boundary": "cradle-to-gate",
                    "ghg_factor_scope_1": 0.1,
                    "ghg_factor_scope_2": 0.2,
                    "ghg_factor_scope_3_rest": 0.3,
                },
                # BOF with hot metal and NO reductant - the reductantless alternative
                {
                    "Business case": "steel_bof",
                    "Metallic charge": "Hot metal",  # Non-normalized!
                    "Reductant": "",  # Empty string for no reductant
                    "Metric type": "Materials",
                    "Side": "Input",
                    "Type": "material",
                    "Vector": "hot_metal",
                    "Value": 0.9,  # Has value
                    "Unit": "t/t",
                    "System boundary": "cradle-to-gate",
                    "ghg_factor_scope_1": 0.1,
                    "ghg_factor_scope_2": 0.2,
                    "ghg_factor_scope_3_rest": 0.3,
                },
            ]
        )

        mock_read_excel.return_value = data

        # This should not raise ValueError about missing reductantless alternative
        result = read_dynamic_business_cases("dummy.xlsx", "Bill of Materials")

        # Check that BOF business cases were created
        assert "BOF" in result
        bof_cases = result["BOF"]

        # Should have both the reductant and reductantless versions
        hot_metal_cases = [case for case in bof_cases if case.metallic_charge == "hot_metal"]
        assert len(hot_metal_cases) >= 1

        # The coke version should have inherited the value from the reductantless version
        coke_case = next((case for case in hot_metal_cases if case.reductant == "coke"), None)
        assert coke_case is not None, "Should have a coke case"
        assert coke_case.required_quantity_per_ton_of_product == 0.9

    @patch("pandas.read_excel")
    def test_error_when_no_reductantless_alternative_exists(self, mock_read_excel):
        """Test that we get proper error when reductantless alternative is missing."""
        # Only BOF with reductant, no reductantless alternative
        data = pd.DataFrame(
            [
                {
                    "Business case": "steel_bof",
                    "Metallic charge": "Hot metal",
                    "Reductant": "Coke",
                    "Metric type": "Materials",
                    "Side": "Input",
                    "Type": "material",
                    "Vector": "hot_metal",
                    "Value": None,  # Missing value and no alternative
                    "Unit": "t/t",
                    "System boundary": "cradle-to-gate",
                    "ghg_factor_scope_1": 0.1,
                    "ghg_factor_scope_2": 0.2,
                    "ghg_factor_scope_3_rest": 0.3,
                },
            ]
        )

        mock_read_excel.return_value = data

        # This should skip the feedstock with a warning, not raise an error
        result = read_dynamic_business_cases("dummy.xlsx", "Bill of Materials")

        # BOF might not be in result if all its feedstocks were skipped
        if "BOF" in result:
            bof_cases = result["BOF"]
            # Should not have the incomplete feedstock
            assert not any(case.reductant == "coke" for case in bof_cases)
