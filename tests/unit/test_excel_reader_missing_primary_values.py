"""Test for handling feedstocks with missing primary values."""

import pandas as pd
from unittest.mock import patch
from steelo.adapters.dataprocessing.excel_reader import read_dynamic_business_cases


class TestMissingPrimaryValues:
    """Test handling when feedstocks have missing primary values."""

    @patch("pandas.read_excel")
    def test_feedstock_skipped_when_primary_value_missing(self, mock_read_excel):
        """Test that feedstocks without primary values are skipped."""
        # Create test data where both BOF entries have no value
        data = pd.DataFrame(
            [
                # BOF with hot metal and coke - no value
                {
                    "Business case": "steel_bof",
                    "Metallic charge": "Hot metal",
                    "Reductant": "Coke",
                    "Metric type": "Materials",
                    "Side": "Input",
                    "Type": "feedstock",
                    "Vector": "hot_metal",
                    "Value": float("nan"),  # No value
                    "Unit": "t/t",
                    "System boundary": "cradle-to-gate",
                    "ghg_factor_scope_1": 0.1,
                    "ghg_factor_scope_2": 0.2,
                    "ghg_factor_scope_3_rest": 0.3,
                },
                # BOF with hot metal without reductant - also no value
                {
                    "Business case": "steel_bof",
                    "Metallic charge": "Hot metal",
                    "Reductant": "",  # Empty reductant
                    "Metric type": "Materials",
                    "Side": "Input",
                    "Type": "feedstock",
                    "Vector": "hot_metal",
                    "Value": float("nan"),  # Also no value!
                    "Unit": "t/t",
                    "System boundary": "cradle-to-gate",
                    "ghg_factor_scope_1": 0.1,
                    "ghg_factor_scope_2": 0.2,
                    "ghg_factor_scope_3_rest": 0.3,
                },
                # Add a valid EAF case so we have some output
                {
                    "Business case": "steel_eaf",
                    "Metallic charge": "Scrap",
                    "Reductant": "",
                    "Metric type": "Materials",
                    "Side": "Input",
                    "Type": "feedstock",
                    "Vector": "scrap",
                    "Value": 1.0,
                    "Unit": "t/t",
                    "System boundary": "cradle-to-gate",
                    "ghg_factor_scope_1": 0.1,
                    "ghg_factor_scope_2": 0.2,
                    "ghg_factor_scope_3_rest": 0.3,
                },
            ]
        )

        mock_read_excel.return_value = data

        # This should not raise ValueError - both BOF feedstocks should be skipped
        result = read_dynamic_business_cases("dummy.xlsx", "Bill of Materials")

        # BOF should not be in result since all its feedstocks were skipped
        assert "BOF" not in result

        # EAF should still be there
        assert "EAF" in result
        assert len(result["EAF"]) == 1
        assert result["EAF"][0].required_quantity_per_ton_of_product == 1.0

    @patch("pandas.read_excel")
    def test_warning_logged_when_primary_value_missing(self, mock_read_excel, caplog):
        """Test that warnings are logged when feedstocks have missing primary values."""
        # Same data as above
        data = pd.DataFrame(
            [
                {
                    "Business case": "steel_bof",
                    "Metallic charge": "Hot metal",
                    "Reductant": "Coke",
                    "Metric type": "Materials",
                    "Side": "Input",
                    "Type": "feedstock",
                    "Vector": "hot_metal",
                    "Value": float("nan"),
                    "Unit": "t/t",
                    "System boundary": "cradle-to-gate",
                    "ghg_factor_scope_1": 0.1,
                    "ghg_factor_scope_2": 0.2,
                    "ghg_factor_scope_3_rest": 0.3,
                },
                {
                    "Business case": "steel_bof",
                    "Metallic charge": "Hot metal",
                    "Reductant": "",
                    "Metric type": "Materials",
                    "Side": "Input",
                    "Type": "feedstock",
                    "Vector": "hot_metal",
                    "Value": float("nan"),
                    "Unit": "t/t",
                    "System boundary": "cradle-to-gate",
                    "ghg_factor_scope_1": 0.1,
                    "ghg_factor_scope_2": 0.2,
                    "ghg_factor_scope_3_rest": 0.3,
                },
            ]
        )

        mock_read_excel.return_value = data

        with caplog.at_level("WARNING"):
            read_dynamic_business_cases("dummy.xlsx", "Bill of Materials")

        # Should have warnings about feedstocks being skipped due to no primary value
        warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
        # Check that we have warnings about skipping feedstocks
        assert any("Skipping feedstock" in w and "no primary value" in w for w in warnings)
        # Should have at least one warning (for the feedstock with coke)
        assert len(warnings) >= 1
