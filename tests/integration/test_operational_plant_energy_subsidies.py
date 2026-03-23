"""Integration tests for energy carrier subsidies applied to operational plants.

Tests the simulation.py path that applies energy carrier subsidies to
FurnaceGroup energy_costs during yearly simulation.
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


def apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year):
    """
    Apply energy carrier subsidies to a FurnaceGroup.

    Mirrors the generic logic in simulation.py.

    Args:
        fg: FurnaceGroup to apply subsidies to.
        iso3: Country code for the plant location.
        energy_subsidies: Dict of {carrier: {iso3: {tech: [Subsidy, ...]}}}.
        year: Current simulation year.
    """
    active_energy_subs: dict[str, list] = {}
    for carrier, carrier_subs in energy_subsidies.items():
        all_subs = carrier_subs.get(iso3, {}).get(fg.technology.name, [])
        active = list(filter_subsidies_for_year(all_subs, year))
        if active:
            active_energy_subs[carrier] = active

    if active_energy_subs:
        input_costs, output_costs, no_subsidy_prices = get_subsidised_energy_costs(
            fg.energy_costs,
            active_energy_subs,
        )
        fg.set_subsidised_energy_costs(
            input_costs,
            output_costs,
            no_subsidy_prices,
            active_energy_subs,
        )


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
        subsidy_amount=1000.0,
    )
    energy_subsidies = {"hydrogen": {"USA": {"DRI": [h2_subsidy]}}}

    # Apply subsidies
    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

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
    energy_subsidies = {"electricity": {"USA": {"DRI": [elec_subsidy]}}}

    # Apply subsidies
    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

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
        subsidy_amount=2000.0,
    )
    elec_subsidy = Subsidy(
        scenario_name="test_elec",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="electricity",
        subsidy_type="absolute",
        subsidy_amount=0.05,
    )
    energy_subsidies = {
        "hydrogen": {"USA": {"DRI": [h2_subsidy]}},
        "electricity": {"USA": {"DRI": [elec_subsidy]}},
    }

    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    # Both should be reduced
    assert fg.energy_costs["hydrogen"] == 3000.0, "H2: 5000.0 - 2000.0 = 3000.0"
    assert fg.energy_costs["electricity"] == 0.05, "Elec: 0.10 - 0.05 = 0.05"
    # Both originals tracked
    assert fg.energy_costs_no_subsidy["hydrogen"] == 5000.0
    assert fg.energy_costs_no_subsidy["electricity"] == 0.10
    # Both subsidies tracked
    assert len(fg.applied_subsidies["hydrogen"]) == 1
    assert len(fg.applied_subsidies["electricity"]) == 1


def test_natural_gas_subsidy_applied_to_furnace_group(plant_with_fg_in_usa):
    """Verify natural gas subsidy reduces energy_costs."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2025)

    ng_subsidy = Subsidy(
        scenario_name="test_ng",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="natural_gas",
        subsidy_type="absolute",
        subsidy_amount=0.01,
    )
    energy_subsidies = {"natural_gas": {"USA": {"DRI": [ng_subsidy]}}}

    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    assert fg.energy_costs["natural_gas"] == pytest.approx(0.02), "NG: 0.03 - 0.01 = 0.02"
    assert fg.energy_costs_no_subsidy["natural_gas"] == 0.03
    assert len(fg.applied_subsidies["natural_gas"]) == 1
    # Other carriers unchanged
    assert fg.energy_costs["hydrogen"] == 5000.0
    assert fg.energy_costs["electricity"] == 0.10


def test_bio_pci_subsidy_applied_to_furnace_group(plant_with_fg_in_usa):
    """Verify bio_pci subsidy reduces energy_costs for material carriers (USD/t)."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    fg.energy_costs["bio_pci"] = 300.0  # USD/t
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2025)

    bio_pci_subsidy = Subsidy(
        scenario_name="test_bio_pci",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="bio_pci",
        subsidy_type="relative",
        subsidy_amount=0.25,  # 25% reduction
    )
    energy_subsidies = {"bio_pci": {"USA": {"DRI": [bio_pci_subsidy]}}}

    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    assert fg.energy_costs["bio_pci"] == pytest.approx(225.0), "bio_pci: 300 - 75 = 225"
    assert fg.energy_costs_no_subsidy["bio_pci"] == 300.0
    assert len(fg.applied_subsidies["bio_pci"]) == 1


def test_coal_subsidy_applied_to_furnace_group(plant_with_fg_in_usa):
    """Verify coal subsidy reduces energy_costs."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    fg.energy_costs["coal"] = 0.025  # USD/kWh
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2025)

    coal_subsidy = Subsidy(
        scenario_name="test_coal",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="coal",
        subsidy_type="absolute",
        subsidy_amount=0.005,  # USD/kWh
    )
    energy_subsidies = {"coal": {"USA": {"DRI": [coal_subsidy]}}}

    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    assert fg.energy_costs["coal"] == pytest.approx(0.020), "coal: 0.025 - 0.005 = 0.020"
    assert fg.energy_costs_no_subsidy["coal"] == 0.025
    assert len(fg.applied_subsidies["coal"]) == 1


def test_multiple_carrier_subsidies_simultaneously(plant_with_fg_in_usa):
    """Verify subsidies for H2, electricity, natural_gas, and bio_pci all apply together."""
    fg = plant_with_fg_in_usa.furnace_groups[0]
    fg.energy_costs["bio_pci"] = 300.0  # USD/t
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
        subsidy_amount=500.0,
    )
    elec_subsidy = Subsidy(
        scenario_name="test_elec",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="electricity",
        subsidy_type="relative",
        subsidy_amount=0.1,
    )
    ng_subsidy = Subsidy(
        scenario_name="test_ng",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="natural_gas",
        subsidy_type="absolute",
        subsidy_amount=0.005,
    )
    bio_pci_subsidy = Subsidy(
        scenario_name="test_bio_pci",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="bio_pci",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    energy_subsidies = {
        "hydrogen": {"USA": {"DRI": [h2_subsidy]}},
        "electricity": {"USA": {"DRI": [elec_subsidy]}},
        "natural_gas": {"USA": {"DRI": [ng_subsidy]}},
        "bio_pci": {"USA": {"DRI": [bio_pci_subsidy]}},
    }

    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    assert fg.energy_costs["hydrogen"] == 4500.0, "H2: 5000 - 500 = 4500"
    assert fg.energy_costs["electricity"] == pytest.approx(0.09), "Elec: 0.10 - 10% = 0.09"
    assert fg.energy_costs["natural_gas"] == pytest.approx(0.025), "NG: 0.03 - 0.005 = 0.025"
    assert fg.energy_costs["bio_pci"] == pytest.approx(250.0), "bio_pci: 300 - 50 = 250"
    # All originals tracked
    assert fg.energy_costs_no_subsidy["hydrogen"] == 5000.0
    assert fg.energy_costs_no_subsidy["electricity"] == 0.10
    assert fg.energy_costs_no_subsidy["natural_gas"] == 0.03
    assert fg.energy_costs_no_subsidy["bio_pci"] == 300.0


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
        subsidy_amount=1000.0,
    )
    energy_subsidies = {"hydrogen": {"DEU": {"DRI": [h2_subsidy]}}}

    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    # No change - USA plant doesn't match DEU subsidy
    assert fg.energy_costs["hydrogen"] == 5000.0
    assert "hydrogen" not in fg.applied_subsidies


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
        subsidy_amount=1000.0,
    )
    energy_subsidies = {"hydrogen": {"USA": {"BOF": [h2_subsidy]}}}

    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    # No change - DRI doesn't match BOF subsidy
    assert fg.energy_costs["hydrogen"] == 5000.0
    assert "hydrogen" not in fg.applied_subsidies


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
        subsidy_amount=1000.0,
    )
    energy_subsidies = {"hydrogen": {"USA": {"DRI": [h2_subsidy]}}}

    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    # No change - year 2035 is outside 2020-2030 range
    assert fg.energy_costs["hydrogen"] == 5000.0
    assert "hydrogen" not in fg.applied_subsidies


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
        subsidy_amount=10000.0,  # greater than $5000 price
    )
    energy_subsidies = {"hydrogen": {"USA": {"DRI": [h2_subsidy]}}}

    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    # Price floors at zero
    assert fg.energy_costs["hydrogen"] == 0.0
    assert fg.energy_costs_no_subsidy["hydrogen"] == 5000.0


def test_get_subsidised_energy_costs_with_non_h2_elec_carrier():
    """Regression: get_subsidised_energy_costs must not KeyError when carrier is not H2/electricity.

    Prior to the temp_costs fix, a 2-key dict {"hydrogen", "electricity"} was passed,
    causing KeyError for any other carrier subsidy (e.g. natural_gas).
    """
    full_energy_costs = {
        "hydrogen": 5000.0,
        "electricity": 0.10,
        "natural_gas": 0.03,
        "coal": 0.025,
    }
    ng_sub = Subsidy(
        scenario_name="ng_test",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="natural_gas",
        subsidy_type="absolute",
        subsidy_amount=0.005,
    )
    # Must not raise KeyError
    input_costs, output_costs, no_sub = get_subsidised_energy_costs(
        full_energy_costs,
        {"natural_gas": [ng_sub]},
    )
    assert input_costs["natural_gas"] == pytest.approx(0.025)
    assert output_costs["natural_gas"] == pytest.approx(0.035)  # 0.03 + 0.005
    assert no_sub["natural_gas"] == 0.03
    # Unsubsidised carriers unchanged
    assert input_costs["hydrogen"] == 5000.0
    assert input_costs["coal"] == 0.025


def test_subsidy_reduces_input_cost_and_increases_output_profit(plant_with_fg_in_usa):
    """Verify dual-sided subsidy: input cost reduced, output profit increased.

    A subsidy simultaneously reduces the input price (cheaper to consume) and
    increases the output price (more profitable to produce as by-product).
    """
    fg = plant_with_fg_in_usa.furnace_groups[0]
    iso3 = plant_with_fg_in_usa.location.iso3
    year = Year(2025)

    # Set base prices via set_energy_costs (populates both energy_costs and output_energy_costs)
    fg.set_energy_costs(co2_stored=50.0, electricity=0.10)

    co2_subsidy = Subsidy(
        scenario_name="45Q",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="co2_stored",
        subsidy_type="absolute",
        subsidy_amount=25.0,
    )
    energy_subsidies = {"co2_stored": {"USA": {"DRI": [co2_subsidy]}}}
    apply_energy_subsidies_to_fg(fg, iso3, energy_subsidies, year)

    # Input cost reduced: 50 - 25 = 25
    assert fg.energy_costs["co2_stored"] == pytest.approx(25.0)
    # Output profit increased: 50 + 25 = 75
    assert fg.output_energy_costs["co2_stored"] == pytest.approx(75.0)
    # Original price preserved
    assert fg.energy_costs_no_subsidy["co2_stored"] == 50.0

    # Negative co2_stored (credit) — subsidy still applied
    fg2 = plant_with_fg_in_usa.furnace_groups[0]
    fg2.set_energy_costs(co2_stored=-50.0, electricity=0.10)
    fg2.energy_costs_no_subsidy = {}
    fg2.applied_subsidies = {}
    apply_energy_subsidies_to_fg(fg2, iso3, energy_subsidies, year)

    assert fg2.energy_costs["co2_stored"] == pytest.approx(0.0)  # max(0, -50 - 25) = 0
    assert fg2.output_energy_costs["co2_stored"] == pytest.approx(-25.0)  # -50 + 25 = -25
