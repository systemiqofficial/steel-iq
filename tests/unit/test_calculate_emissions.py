import pytest

from steelo.domain import calculate_emissions
from steelo.domain.models import PrimaryFeedstock, TechnologyEmissionFactors


def _feedstock(metallic_charge: str) -> PrimaryFeedstock:
    feedstock = PrimaryFeedstock(metallic_charge=metallic_charge, reductant="electricity", technology="EAF")
    feedstock.required_quantity_per_ton_of_product = 1.0
    return feedstock


def _factor(metallic_charge: str, boundary: str) -> TechnologyEmissionFactors:
    return TechnologyEmissionFactors(
        business_case="steel_eaf",
        technology="EAF",
        boundary=boundary,
        metallic_charge=metallic_charge,
        reductant="electricity",
        direct_ghg_factor=0.1,
        direct_with_biomass_ghg_factor=0.1,
        indirect_ghg_factor=0.2,
    )


def test_calculate_emissions_adds_grid_emissions_once_for_multiple_charges():
    """Grid emissions enter indirect_ghg once per boundary, however many metallic charges the group has."""
    charges = ["scrap", "dri_high", "hbi_low"]
    boundaries = ["plant_boundary", "supply_chain"]

    emissions = calculate_emissions.calculate_emissions(
        material_bill={charge: {"demand": 100.0} for charge in charges},
        business_cases={charge: _feedstock(charge) for charge in charges},
        technology_emission_factors=[_factor(charge, boundary) for charge in charges for boundary in boundaries],
        grid_emissions=50.0,
    )

    for boundary in boundaries:
        assert emissions[boundary]["indirect_ghg"] == pytest.approx(3 * 0.2 * 100.0 + 50.0)
        assert emissions[boundary]["direct_ghg"] == pytest.approx(3 * 0.1 * 100.0)
