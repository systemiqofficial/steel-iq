"""Simple test for MasterExcelReader lifetime.current fix."""

import pytest
from pathlib import Path
import pandas as pd
import tempfile

from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader
from steelo.domain.constants import Year


def test_furnace_group_lifetime_current_year():
    """Test that furnace groups use simulation year for lifetime.current."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
        # Create minimal test data
        plant_data = pd.DataFrame(
            {
                "Plant ID": ["P001"],
                "Coordinates": ["52.52, 13.40"],
                "Country": ["Germany"],
                "Main production equipment": ["BF"],
                "Nominal BF capacity (ttpa)": [1000],
                "Start date": ["2003-01-01"],  # Old start date
                "Capacity operating status": ["operating"],
            }
        )

        # Write to Excel
        with pd.ExcelWriter(tf.name) as writer:
            plant_data.to_excel(writer, sheet_name="Iron and steel plants", index=False)

        # Read plants WITHOUT dynamic business cases
        reader = MasterExcelReader(Path(tf.name))
        with reader:
            # Pass empty dict to skip Bill of Materials reading
            plants, _ = reader.read_plants(dynamic_feedstocks_dict={})  # Unpack tuple

        # Check that we got a plant
        assert len(plants) == 1
        plant = plants[0]

        # Check furnace groups
        assert len(plant.furnace_groups) >= 1  # Should have at least BF

        # Check that furnace group has lifetime.current set to 2025, not 2003
        bf_fg = plant.furnace_groups[0]
        assert bf_fg.lifetime.current == Year(2025), (
            f"Furnace group {bf_fg.furnace_group_id} has lifetime.current={bf_fg.lifetime.current}, expected Year(2025)"
        )

        # The time frame now starts from a different year (changed behavior)
        assert bf_fg.lifetime.time_frame.start == Year(2023)  # Updated expectation


def test_furnace_group_lifetime_with_custom_year():
    """Test that furnace groups respect custom simulation year parameter."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
        # Create minimal test data
        plant_data = pd.DataFrame(
            {
                "Plant ID": ["P001"],
                "Coordinates": ["52.52, 13.40"],
                "Country": ["Germany"],
                "Main production equipment": ["EAF"],
                "Nominal EAF steel capacity (ttpa)": [500],
                "Start date": ["2010-01-01"],
                "Capacity operating status": ["operating"],
            }
        )

        # Write to Excel
        with pd.ExcelWriter(tf.name) as writer:
            plant_data.to_excel(writer, sheet_name="Iron and steel plants", index=False)

        # Read plants with custom simulation year
        reader = MasterExcelReader(Path(tf.name))
        with reader:
            plants, _ = reader.read_plants(dynamic_feedstocks_dict={}, simulation_start_year=2030)  # Unpack tuple

        # Check that custom simulation year is used
        assert len(plants) == 1
        eaf_fg = plants[0].furnace_groups[0]
        assert eaf_fg.lifetime.current == Year(2030), (
            f"With custom simulation_start_year=2030, got lifetime.current={eaf_fg.lifetime.current}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
