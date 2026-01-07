from pathlib import Path

from steelo.domain import Year
from steelo.domain.models import Environment
from steelo.simulation import SimulationConfig
from steelo.simulation_types import TechnologySettings


def test_load_allowed_transitions_keeps_future_techs(tmp_path):
    tech_settings = {
        "BF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "BFCCS": TechnologySettings(allowed=True, from_year=2035, to_year=None),
    }

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        technology_settings=tech_settings,
    )

    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF,BF+CCS\nBF,YES,YES\n", encoding="utf-8")

    env = Environment(config=config, tech_switches_csv=tech_switches_csv)

    assert env.allowed_furnace_transitions["BF"] == ["BF", "BF+CCS"]
