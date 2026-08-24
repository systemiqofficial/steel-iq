"""Grid emissivity propagation resolves plant locations finest-available first."""

from pathlib import Path

import pytest

from steelo.devdata import get_plant
from steelo.domain.models import Environment, Location, RegionEmissivity, Year
from steelo.simulation import SimulationConfig


def make_env(tmp_path) -> Environment:
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2027),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF\nBF,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    env.year = Year(2025)
    return env


def region(iso3, geo_unit=None, years=None, scenario="Business As Usual"):
    return RegionEmissivity(
        iso3=iso3,
        geo_unit=geo_unit,
        country_name="China",
        scenario=scenario,
        grid_emissivity={Year(year): {"Electricity": value} for year, value in (years or {}).items()},
        coke_emissivity={},
        gas_emissivity={},
    )


def chn_plant(geo_unit=None):
    location = Location(iso3="CHN", country="China", region="Asia", lat=30.0, lon=110.0, geo_unit=geo_unit)
    return get_plant(location=location)


def test_plant_with_geo_unit_gets_provincial_value(tmp_path):
    """A plant in an authored province gets the provincial value, others the country value."""
    env = make_env(tmp_path)
    env.initiate_grid_emissivity(
        emissivities=[region("CHN", years={2025: 0.5}), region("CHN", "CN-AH", years={2025: 0.3})]
    )
    provincial, national = chn_plant(geo_unit="CN-AH"), chn_plant()

    env.propagate_grid_emissivity_to_furnace_groups(plants=[provincial, national])

    assert all(fg.grid_emissivity == 0.3 for fg in provincial.furnace_groups)
    assert all(fg.grid_emissivity == 0.5 for fg in national.furnace_groups)


def test_plant_with_unauthored_geo_unit_falls_back_to_country(tmp_path):
    """A plant in a province without its own entry falls back to the country value."""
    env = make_env(tmp_path)
    env.initiate_grid_emissivity(emissivities=[region("CHN", years={2025: 0.5})])
    plant = chn_plant(geo_unit="CN-HE")

    env.propagate_grid_emissivity_to_furnace_groups(plants=[plant])

    assert all(fg.grid_emissivity == 0.5 for fg in plant.furnace_groups)


def test_missing_country_raises(tmp_path):
    """A plant whose location has no entry at all fails loudly, never a silent 0.0."""
    env = make_env(tmp_path)
    env.initiate_grid_emissivity(emissivities=[region("CHN", years={2025: 0.5})])
    location = Location(iso3="IND", country="India", region="Asia", lat=20.0, lon=77.0)
    plant = get_plant(location=location)

    with pytest.raises(ValueError, match="Grid emissivity not found for IND"):
        env.propagate_grid_emissivity_to_furnace_groups(plants=[plant])


def test_missing_year_raises(tmp_path):
    """An entry without the current year fails loudly, never a silent 0.0."""
    env = make_env(tmp_path)
    env.initiate_grid_emissivity(emissivities=[region("CHN", years={2025: 0.5})])
    env.year = Year(2026)

    with pytest.raises(ValueError, match="Grid emissivity not found for CHN in year 2026"):
        env.propagate_grid_emissivity_to_furnace_groups(plants=[chn_plant()])


def test_scenario_mismatch_raises_on_initiate(tmp_path):
    """Supplied emissivities matching no chosen scenario fail loudly at initiation."""
    env = make_env(tmp_path)

    with pytest.raises(ValueError, match="No grid emissivities match scenario 'Business As Usual'"):
        env.initiate_grid_emissivity(emissivities=[region("CHN", years={2025: 0.5}, scenario="Net Zero")])


def test_empty_emissivities_skip_propagation(tmp_path):
    """No emissivity data at all (test fixtures) skips propagation without raising."""
    env = make_env(tmp_path)
    env.initiate_grid_emissivity(emissivities=[])
    plant = chn_plant()

    env.propagate_grid_emissivity_to_furnace_groups(plants=[plant])

    assert all(fg.grid_emissivity is None for fg in plant.furnace_groups)
