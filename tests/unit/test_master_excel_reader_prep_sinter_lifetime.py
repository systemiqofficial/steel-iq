"""Test that Prep Sinter furnace groups get correct lifetime.current."""

import pytest
from pathlib import Path
import pandas as pd
import tempfile

from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader
from steelo.domain.constants import Year


def test_prep_sinter_lifetime_current():
    """Test that Prep Sinter furnace groups inherit correct lifetime.current."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
        # Create plant with iron-making technology (BF)
        plant_data = pd.DataFrame(
            {
                "Plant ID": ["P001"],
                "Coordinates": ["52.52, 13.40"],
                "Country": ["Germany"],
                "Main production equipment": ["BF"],  # Iron-making tech should get Prep Sinter
                "Nominal BF capacity (ttpa)": [1000],
                "Start date": ["2010-01-01"],
                "Capacity operating status": ["operating"],
            }
        )

        # Write to Excel
        with pd.ExcelWriter(tf.name) as writer:
            plant_data.to_excel(writer, sheet_name="Iron and steel plants", index=False)

        # Read plants
        reader = MasterExcelReader(Path(tf.name))
        with reader:
            plants, _ = reader.read_plants(dynamic_feedstocks_dict={}, simulation_start_year=2025)  # Unpack tuple

        plant = plants[0]

        # Should only have BF (Prep Sinter no longer auto-added)
        assert len(plant.furnace_groups) == 1

        # Find the BF furnace group
        bf_fg = plant.furnace_groups[0]
        assert bf_fg.technology.name == "BF"
        # prep_sinter_fg = None  # No longer added - commenting out unused variable

        assert bf_fg is not None, "BF furnace group not found"
        # Prep Sinter is no longer auto-added, so we skip the rest of the test

        # BF should have lifetime.current = 2025
        assert bf_fg.lifetime.current == Year(2025)

        # BF time frame start should be the plant start date
        assert bf_fg.lifetime.time_frame.start == Year(2010)  # Plant started in 2010


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
