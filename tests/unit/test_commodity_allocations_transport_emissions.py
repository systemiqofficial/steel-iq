import logging
from unittest.mock import Mock


from steelo.domain.constants import Volumes
from steelo.domain.models import (
    CommodityAllocations,
    DemandCenter,
    FurnaceGroup,
    Location,
    Plant,
    Supplier,
    TransportKPI,
)


def make_plant_and_furnace_group(iso3: str) -> tuple[Plant, FurnaceGroup]:
    """Build a mocked (Plant, FurnaceGroup) allocation node located in one country.

    Args:
        iso3: Country of the plant.

    Returns:
        A (Plant, FurnaceGroup) tuple of spec mocks.
    """
    plant = Mock(spec=Plant)
    plant.location = Mock(spec=Location)
    plant.location.iso3 = iso3
    furnace_group = Mock(spec=FurnaceGroup)
    return plant, furnace_group


def make_supplier(iso3: str) -> Supplier:
    """Build a mocked supplier located in one country.

    Args:
        iso3: Country of the supplier.

    Returns:
        A Supplier spec mock with a location.

    Notes:
        `Supplier.location` is set in `__init__`, so the spec mock lacks it until assigned here.
    """
    supplier = Mock(spec=Supplier)
    supplier.location = Mock(spec=Location)
    supplier.location.iso3 = iso3
    return supplier


def make_transport_kpi(reporter_iso: str, partner_iso: str, commodity: str, ghg_factor: float) -> TransportKPI:
    """Build a transport record for one route.

    Args:
        reporter_iso: Origin country.
        partner_iso: Destination country.
        commodity: Commodity shipped.
        ghg_factor: Emission factor of the route.

    Returns:
        A TransportKPI with a zero transportation cost.
    """
    return TransportKPI(
        reporter_iso=reporter_iso,
        partner_iso=partner_iso,
        commodity=commodity,
        ghg_factor=ghg_factor,
        transportation_cost=0.0,
        updated_on="2026-10-09",
    )


def test_update_transport_emissions_sums_factor_times_volume_over_sources():
    """A furnace group fed by a supplier and another furnace group gets the sum of factor times volume."""
    supplier = make_supplier("AUS")
    source_plant, source_fg = make_plant_and_furnace_group("BRA")
    destination = make_plant_and_furnace_group("DEU")
    allocations = {
        supplier: {destination: Volumes(100.0)},
        (source_plant, source_fg): {destination: Volumes(50.0)},
    }
    transport_emissions = [
        make_transport_kpi("AUS", "DEU", "io_high", 0.5),
        make_transport_kpi("BRA", "DEU", "io_high", 0.2),
        make_transport_kpi("AUS", "DEU", "scrap", 9.0),
        make_transport_kpi("DEU", "AUS", "io_high", 9.0),
    ]

    CommodityAllocations(commodity="io_high", allocations=allocations).update_transport_emissions(
        transport_emissions,
    )

    _, destination_fg = destination
    assert destination_fg.transport_emissions == 0.5 * 100.0 + 0.2 * 50.0


def test_update_transport_emissions_closely_allocated_commodity_is_zero():
    """Hot metal contributes zero even on a cross-border route with a recorded factor."""
    source = make_plant_and_furnace_group("CHN")
    destination = make_plant_and_furnace_group("JPN")
    allocations = {source: {destination: Volumes(200.0)}}
    transport_emissions = [make_transport_kpi("CHN", "JPN", "hot_metal", 0.3)]

    CommodityAllocations(commodity="hot_metal", allocations=allocations).update_transport_emissions(
        transport_emissions,
    )

    _, destination_fg = destination
    assert destination_fg.transport_emissions == 0.0


def test_update_transport_emissions_domestic_route_is_zero():
    """A domestic route contributes zero, so it needs no transport record."""
    supplier = make_supplier("DEU")
    destination = make_plant_and_furnace_group("DEU")
    allocations = {supplier: {destination: Volumes(100.0)}}
    transport_emissions = [make_transport_kpi("AUS", "DEU", "scrap", 0.5)]

    CommodityAllocations(commodity="scrap", allocations=allocations).update_transport_emissions(
        transport_emissions,
    )

    _, destination_fg = destination
    assert destination_fg.transport_emissions == 0.0


def test_update_transport_emissions_unrecorded_route_is_zero_and_warns(caplog):
    """A cross-border route without a record for the commodity contributes zero and is named in a warning."""
    supplier = make_supplier("SWE")
    destination = make_plant_and_furnace_group("DEU")
    allocations = {supplier: {destination: Volumes(100.0)}}
    transport_emissions = [
        make_transport_kpi("SWE", "DEU", "steel", 0.2),
        make_transport_kpi("SWE", "POL", "io_high", 9.0),
    ]

    with caplog.at_level(logging.WARNING):
        CommodityAllocations(commodity="io_high", allocations=allocations).update_transport_emissions(
            transport_emissions,
        )

    _, destination_fg = destination
    assert destination_fg.transport_emissions == 0.0
    assert "SWE->DEU" in caplog.text


def test_update_transport_emissions_ignores_demand_centre_destinations():
    """A demand-centre destination gets no transport emissions and adds nothing to furnace groups."""
    supplier = make_supplier("AUS")
    demand_centre = Mock(spec=DemandCenter)
    destination = make_plant_and_furnace_group("DEU")
    allocations = {
        supplier: {
            demand_centre: Volumes(1000.0),
            destination: Volumes(10.0),
        },
    }
    transport_emissions = [make_transport_kpi("AUS", "DEU", "steel", 0.4)]

    CommodityAllocations(commodity="steel", allocations=allocations).update_transport_emissions(
        transport_emissions,
    )

    _, destination_fg = destination
    assert destination_fg.transport_emissions == 0.4 * 10.0
    assert not hasattr(demand_centre, "transport_emissions")


def test_update_transport_emissions_does_not_double_count_on_repeat_call():
    """Calling the method twice resets the furnace group first, so the value does not accumulate."""
    supplier = make_supplier("AUS")
    destination = make_plant_and_furnace_group("DEU")
    allocations = {supplier: {destination: Volumes(100.0)}}
    transport_emissions = [make_transport_kpi("AUS", "DEU", "hbi_high", 0.25)]
    commodity_allocations = CommodityAllocations(commodity="hbi_high", allocations=allocations)

    commodity_allocations.update_transport_emissions(transport_emissions)
    commodity_allocations.update_transport_emissions(transport_emissions)

    _, destination_fg = destination
    assert destination_fg.transport_emissions == 0.25 * 100.0
