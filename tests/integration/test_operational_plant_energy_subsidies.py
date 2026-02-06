"""Integration tests for H2/electricity subsidies applied to operational plants.

Tests the simulation.py path (lines 1014-1029) that applies H2/electricity
subsidies to FurnaceGroup energy_costs during yearly simulation.
"""

import pytest

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, Year, TimeFrame, Subsidy
from steelo.domain.calculate_costs import filter_subsidies_for_year, get_subsidised_energy_costs


@pytest.fixture
def furnace_group_with_energy_costs():
    """Create a FurnaceGroup with energy_costs set."""
    fg = get_furnace_group(
        fg_id="fg_test_h2_elec",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2023), end=Year(2043)),
            plant_lifetime=20,
        ),
        capacity=100,
        tech_name="DRI",
    )
    fg.energy_costs = {
        "hydrogen": 5000.0,  # USD/t (realistic LCOH after kg->t conversion)
        "electricity": 0.10,  # USD/kWh
        "natural_gas": 0.03,  # USD/kWh (converted from GJ in Excel)
    }
    return fg


@pytest.fixture
def plant_with_fg_in_usa(furnace_group_with_energy_costs):
    """Create a plant in USA with the furnace group."""
    plant = get_plant(
        furnace_groups=[furnace_group_with_energy_costs],
        plant_id="plant_test_h2_elec",
    )
    plant.location.iso3 = "USA"
    return plant


def apply_energy_subsidies_to_fg(fg, iso3, hydrogen_subsidies, electricity_subsidies, year):
    """
    Apply H2/electricity subsidies to a FurnaceGroup.

    Mirrors the logic in simulation.py lines 1014-1029.

    Args:
        fg: FurnaceGroup to apply subsidies to.
        iso3: Country code for the plant location.
        hydrogen_subsidies: Dict of {iso3: {tech: [Subsidy, ...]}}.
        electricity_subsidies: Dict of {iso3: {tech: [Subsidy, ...]}}.
        year: Current simulation year.
    """
    all_h2_subs = hydrogen_subsidies.get(iso3, {}).get(fg.technology.name, [])
    all_elec_subs = electricity_subsidies.get(iso3, {}).get(fg.technology.name, [])
    active_h2_subs = list(filter_subsidies_for_year(all_h2_subs, year))
    active_elec_subs = list(filter_subsidies_for_year(all_elec_subs, year))

    if active_h2_subs or active_elec_subs:
        subsidised_costs, no_subsidy_prices = get_subsidised_energy_costs(
            fg.energy_costs, active_h2_subs, active_elec_subs
        )
        fg.set_subsidised_energy_costs(subsidised_costs, no_subsidy_prices, active_h2_subs, active_elec_subs)


def test_h2_subsidy_applied_to_furnace_group(plant_with_fg_in_usa):
    """Verify H2 subsidy reduces energy_costs and tracks original prices."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2025)

    # Create H2 subsidy: $1000/t absolute for USA/DRI
    h2_subsidy = Subsidy(
        scenario_name="test_h2",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,  # USD/t
    )
    hydrogen_subsidies = {"USA": {"DRI": [h2_subsidy]}}
    electricity_subsidies = {}

    # Apply subsidies
    apply_energy_subsidies_to_fg(fg, iso3, hydrogen_subsidies, electricity_subsidies, year)

    # Verify subsidised price
    assert fg.energy_costs["hydrogen"] == 4000.0, "H2 price should be reduced by $1000/t"
    # Verify original price tracked
    assert fg.energy_costs_no_subsidy["hydrogen"] == 5000.0, "Original H2 price should be tracked"
    # Verify subsidy tracked
    assert len(fg.applied_subsidies["hydrogen"]) == 1
    assert fg.applied_subsidies["hydrogen"][0] == h2_subsidy
    # Verify electricity unchanged
    assert fg.energy_costs["electricity"] == 0.10


def test_electricity_subsidy_applied_to_furnace_group(plant_with_fg_in_usa):
    """Verify electricity subsidy reduces energy_costs with relative subsidy."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2025)

    # Create electricity subsidy: 20% relative for USA/DRI
    elec_subsidy = Subsidy(
        scenario_name="test_elec",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="electricity",
        subsidy_type="relative",
        subsidy_amount=0.2,
    )
    hydrogen_subsidies = {}
    electricity_subsidies = {"USA": {"DRI": [elec_subsidy]}}

    # Apply subsidies
    apply_energy_subsidies_to_fg(fg, iso3, hydrogen_subsidies, electricity_subsidies, year)

    # Verify subsidised price: 0.10 - (0.10 * 0.2) = 0.08
    assert fg.energy_costs["electricity"] == pytest.approx(0.08), "Electricity should be reduced by 20%"
    # Verify original price tracked
    assert fg.energy_costs_no_subsidy["electricity"] == 0.10
    # Verify subsidy tracked
    assert len(fg.applied_subsidies["electricity"]) == 1
    assert fg.applied_subsidies["electricity"][0] == elec_subsidy
    # Verify hydrogen unchanged
    assert fg.energy_costs["hydrogen"] == 5000.0


def test_combined_h2_and_electricity_subsidies(plant_with_fg_in_usa):
    """Verify both H2 and electricity subsidies apply simultaneously."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2025)

    h2_subsidy = Subsidy(
        scenario_name="test_h2",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=2000.0,  # USD/t
    )
    elec_subsidy = Subsidy(
        scenario_name="test_elec",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="electricity",
        subsidy_type="absolute",
        subsidy_amount=0.05,  # USD/kWh
    )
    hydrogen_subsidies = {"USA": {"DRI": [h2_subsidy]}}
    electricity_subsidies = {"USA": {"DRI": [elec_subsidy]}}

    apply_energy_subsidies_to_fg(fg, iso3, hydrogen_subsidies, electricity_subsidies, year)

    # Both should be reduced
    assert fg.energy_costs["hydrogen"] == 3000.0, "H2: 5000.0 - 2000.0 = 3000.0"
    assert fg.energy_costs["electricity"] == 0.05, "Elec: 0.10 - 0.05 = 0.05"
    # Both originals tracked
    assert fg.energy_costs_no_subsidy["hydrogen"] == 5000.0
    assert fg.energy_costs_no_subsidy["electricity"] == 0.10
    # Both subsidies tracked
    assert len(fg.applied_subsidies["hydrogen"]) == 1
    assert len(fg.applied_subsidies["electricity"]) == 1


def test_no_subsidy_when_country_not_matched(plant_with_fg_in_usa):
    """Verify no subsidy applied when iso3 doesn't match."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    iso3 = plant_with_fg_in_usa.location.iso3  # USA
    year = Year(2025)

    # Subsidy for DEU, but plant is in USA
    h2_subsidy = Subsidy(
        scenario_name="test_h2_deu",
        iso3="DEU",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,  # USD/t
    )
    hydrogen_subsidies = {"DEU": {"DRI": [h2_subsidy]}}
    electricity_subsidies = {}

    apply_energy_subsidies_to_fg(fg, iso3, hydrogen_subsidies, electricity_subsidies, year)

    # No change - USA plant doesn't match DEU subsidy
    assert fg.energy_costs["hydrogen"] == 5000.0
    assert fg.applied_subsidies["hydrogen"] == []


def test_no_subsidy_when_tech_not_matched(plant_with_fg_in_usa):
    """Verify no subsidy applied when technology doesn't match."""
    fg = plant_with_fg_in_usa.furnace_groups[0]  # DRI
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2025)

    # Subsidy for BOF, but FG is DRI
    h2_subsidy = Subsidy(
        scenario_name="test_h2_bof",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="BOF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,  # USD/t
    )
    hydrogen_subsidies = {"USA": {"BOF": [h2_subsidy]}}
    electricity_subsidies = {}

    apply_energy_subsidies_to_fg(fg, iso3, hydrogen_subsidies, electricity_subsidies, year)

    # No change - DRI doesn't match BOF subsidy
    assert fg.energy_costs["hydrogen"] == 5000.0
    assert fg.applied_subsidies["hydrogen"] == []


def test_no_subsidy_when_year_outside_range(plant_with_fg_in_usa):
    """Verify no subsidy applied when year is outside subsidy period."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2035)  # Outside subsidy range

    h2_subsidy = Subsidy(
        scenario_name="test_h2",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),  # Ends in 2030
        technology_name="DRI",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,  # USD/t
    )
    hydrogen_subsidies = {"USA": {"DRI": [h2_subsidy]}}
    electricity_subsidies = {}

    apply_energy_subsidies_to_fg(fg, iso3, hydrogen_subsidies, electricity_subsidies, year)

    # No change - year 2035 is outside 2020-2030 range
    assert fg.energy_costs["hydrogen"] == 5000.0
    assert fg.applied_subsidies["hydrogen"] == []


def test_subsidy_floors_price_at_zero(plant_with_fg_in_usa):
    """Verify subsidy exceeding price floors at zero (free energy)."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2025)

    # Subsidy exceeds H2 price
    h2_subsidy = Subsidy(
        scenario_name="test_h2_large",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=10000.0,  # USD/t (greater than $5000 price)
    )
    hydrogen_subsidies = {"USA": {"DRI": [h2_subsidy]}}
    electricity_subsidies = {}

    apply_energy_subsidies_to_fg(fg, iso3, hydrogen_subsidies, electricity_subsidies, year)

    # Price floors at zero
    assert fg.energy_costs["hydrogen"] == 0.0
    assert fg.energy_costs_no_subsidy["hydrogen"] == 5000.0
