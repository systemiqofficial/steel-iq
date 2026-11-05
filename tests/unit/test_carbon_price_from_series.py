"""Tests for the shared carbon price lookup: no series prices at zero, years beyond the
series end reuse its last value, and interior gaps fail loudly."""

from datetime import date

import pytest

from steelo.domain.calculate_costs import carbon_price_from_series
from steelo.domain.calculate_emissions import calculate_emissions_cost_series
from steelo.domain.models import FurnaceGroup, Location, Plant, PointInTime, Technology, Year


SERIES = {Year(2025): 50.0, Year(2026): 60.0, Year(2027): 70.0}


def test_no_series_prices_at_zero():
    assert carbon_price_from_series(None, Year(2030)) == 0.0
    assert carbon_price_from_series({}, Year(2030)) == 0.0


def test_covered_year_reads_the_series():
    assert carbon_price_from_series(SERIES, Year(2026)) == 60.0


def test_year_beyond_series_end_holds_last_value():
    assert carbon_price_from_series(SERIES, Year(2060)) == 70.0


def test_interior_gap_raises():
    with pytest.raises(ValueError, match="no value for 2026"):
        carbon_price_from_series({Year(2025): 50.0, Year(2027): 70.0}, Year(2026))


def test_cosa_carbon_cost_series_holds_last_value_beyond_series_end():
    """The COSA carbon cost series prices years past the series end at its last value, not zero."""
    emissions = {"plant_boundary": {"direct_ghg": 2.0}}

    costs = calculate_emissions_cost_series(emissions, SERIES, "plant_boundary", 2026, 2029)

    assert costs == [120.0, 140.0, 140.0, 140.0]


def test_plant_operating_carbon_cost_holds_last_value_beyond_series_end(monkeypatch):
    """Plant.update_furnace_group_carbon_costs prices a year past the series end at its last value."""
    fg = FurnaceGroup(
        furnace_group_id="fg1",
        capacity=100_000,
        status="operating",
        last_renovation_date=date(2020, 1, 1),
        technology=Technology(name="BF", product="iron"),
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(plant_lifetime=20, current=Year(2025)),
        energy_cost_dict={},
    )
    plant = Plant(
        plant_id="P000000000001",
        location=Location(lat=0.0, lon=0.0, country="Germany", region="europe", iso3="DEU"),
        furnace_groups=[fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="G01",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={"bf": 10.0},
    )
    plant.set_carbon_cost_series(SERIES)
    prices: list[float] = []
    monkeypatch.setattr(fg, "set_carbon_costs_for_emissions", lambda price, boundary: prices.append(price))

    plant.update_furnace_group_carbon_costs(Year(2060), "plant_boundary")

    assert prices == [70.0]
