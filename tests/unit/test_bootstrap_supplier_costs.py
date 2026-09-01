"""Run-time supplier cost configuration applied at bootstrap (steelo.bootstrap.apply_supplier_cost_config)."""

from pathlib import Path

from steelo.bootstrap import apply_supplier_cost_config
from steelo.domain.models import Location, Supplier, Volumes, Year
from steelo.simulation import SimulationConfig


def make_supplier(commodity, *, production_costs, mine_costs=None, mine_prices=None):
    """One supplier of the given commodity with the given cost dictionaries."""
    location = Location(lat=47.5, lon=14.5, country="Austria", region="Europe", iso3="AUT")
    return Supplier(
        supplier_id=f"{commodity}_supplier",
        location=location,
        commodity=commodity,
        capacity_by_year={Year(2025): Volumes(1_000_000)},
        production_cost_by_year=production_costs,
        mine_cost_by_year=mine_costs or {},
        mine_price_by_year=mine_prices or {},
    )


def make_config(tmp_path, **overrides):
    """A minimal SimulationConfig with the given field overrides."""
    return SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        **overrides,
    )


def test_scrap_suppliers_get_the_configured_initial_cost(tmp_path):
    """The prep-time placeholder is replaced for every year with the configured scrap cost."""
    supplier = make_supplier("scrap", production_costs={Year(2025): 450.0, Year(2026): 450.0})
    config = make_config(tmp_path, initial_scrap_production_cost=333.0)

    apply_supplier_cost_config([supplier], config)

    assert supplier.production_cost_by_year == {Year(2025): 333.0, Year(2026): 333.0}


def test_mine_suppliers_follow_the_iron_ore_premium_flag(tmp_path):
    """Mines copy mine prices with premiums enabled and mine costs without."""
    mine_costs = {Year(2025): 40.0}
    mine_prices = {Year(2025): 55.0}

    with_premiums = make_supplier(
        "io_low", production_costs={Year(2025): 1.0}, mine_costs=mine_costs, mine_prices=mine_prices
    )
    apply_supplier_cost_config([with_premiums], make_config(tmp_path, use_iron_ore_premiums=True))
    assert with_premiums.production_cost_by_year == mine_prices

    without_premiums = make_supplier(
        "io_low", production_costs={Year(2025): 1.0}, mine_costs=mine_costs, mine_prices=mine_prices
    )
    apply_supplier_cost_config([without_premiums], make_config(tmp_path, use_iron_ore_premiums=False))
    assert without_premiums.production_cost_by_year == mine_costs


def test_other_suppliers_without_mine_costs_keep_their_costs(tmp_path):
    """A non-scrap supplier with empty mine dictionaries keeps its prepared production costs."""
    supplier = make_supplier("coal", production_costs={Year(2025): 80.0})

    apply_supplier_cost_config([supplier], make_config(tmp_path, use_iron_ore_premiums=True))

    assert supplier.production_cost_by_year == {Year(2025): 80.0}
