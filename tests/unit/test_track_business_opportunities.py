"""Tests for FurnaceGroup.track_business_opportunities method."""

import pytest
from unittest.mock import patch
from steelo.domain.models import Subsidy, Location, TechnologyEmissionFactors, PrimaryFeedstock
from steelo.domain.commands import UpdateFurnaceGroupStatus
from steelo.devdata import get_furnace_group, PointInTime, TimeFrame, Year


@pytest.fixture
def mock_location():
    """Create a mock location."""
    return Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")


@pytest.fixture
def market_price():
    """Create mock market prices for 20 years."""
    return {
        "steel": [600.0] * 22,  # 22 years to cover construction + lifetime
        "iron": [400.0] * 22,
    }


@pytest.fixture
def carbon_costs():
    """Create mock carbon costs."""
    return {Year(y): 50.0 for y in range(2025, 2050)}


@pytest.fixture
def technology_emission_factors():
    """Create mock technology emission factors."""
    return [
        TechnologyEmissionFactors(
            business_case="Scrap",
            technology="EAF",
            boundary="scope_1",
            metallic_charge="scrap",
            reductant="scrap",
            direct_ghg_factor=0.5,
            direct_with_biomass_ghg_factor=0.4,
            indirect_ghg_factor=0.3,
        ),
        TechnologyEmissionFactors(
            business_case="Iron Ore",
            technology="BOF",
            boundary="scope_1",
            metallic_charge="iron_ore",
            reductant="coal",
            direct_ghg_factor=1.5,
            direct_with_biomass_ghg_factor=1.4,
            indirect_ghg_factor=0.4,
        ),
        TechnologyEmissionFactors(
            business_case="Iron Ore",
            technology="DRI",
            boundary="scope_1",
            metallic_charge="iron_ore",
            reductant="natural_gas",
            direct_ghg_factor=0.8,
            direct_with_biomass_ghg_factor=0.7,
            indirect_ghg_factor=0.35,
        ),
    ]


@pytest.fixture
def dynamic_business_cases():
    """Create mock dynamic business cases."""
    return {
        "EAF": [
            PrimaryFeedstock(
                metallic_charge="scrap",
                reductant="scrap",
                technology="EAF",
            )
        ],
        "BOF": [
            PrimaryFeedstock(
                metallic_charge="iron_ore",
                reductant="coal",
                technology="BOF",
            )
        ],
        "DRI": [
            PrimaryFeedstock(
                metallic_charge="iron_ore",
                reductant="natural_gas",
                technology="DRI",
            )
        ],
    }


def test_track_business_opportunity_announce_after_positive_npvs(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test that a business opportunity is announced after consideration_time years of positive NPVs."""
    fg = get_furnace_group(
        fg_id="fg_announce",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Initialize with 2 years of positive NPV already
    fg.historical_npv_business_opportunities = {
        Year(2025): 1000.0,
        Year(2026): 1200.0,
    }

    # Mock calculate_npv_full to return positive NPV
    with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=1500.0):
        # Mock random to always accept announcement
        with patch("random.random", return_value=0.5):
            command = fg.track_business_opportunities(
                year=Year(2027),
                location=mock_location,
                market_price=market_price,
                cost_of_equity=0.08,
                plant_lifetime=20,
                construction_time=2,
                consideration_time=3,
                probability_of_announcement=0.8,  # 80% chance, random returns 0.5
                all_opex_subsidies=[],
                technology_emission_factors=technology_emission_factors,
                chosen_emissions_boundary_for_carbon_costs="scope_1",
                dynamic_business_cases=dynamic_business_cases,
                carbon_costs_for_iso3=carbon_costs,
            )

    # Verify
    assert command is not None
    assert isinstance(command, UpdateFurnaceGroupStatus)
    assert command.new_status == "announced"
    assert len(fg.historical_npv_business_opportunities) == 3
    assert fg.historical_npv_business_opportunities[Year(2027)] == 1500.0


def test_track_business_opportunity_not_announce_due_to_probability(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test that a business opportunity is not announced if random check fails."""
    fg = get_furnace_group(
        fg_id="fg_no_announce",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Initialize with 2 years of positive NPV
    fg.historical_npv_business_opportunities = {
        Year(2025): 1000.0,
        Year(2026): 1200.0,
    }

    # Mock calculate_npv_full to return positive NPV
    with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=1500.0):
        # Mock random to reject announcement
        with patch("random.random", return_value=0.9):
            command = fg.track_business_opportunities(
                year=Year(2027),
                location=mock_location,
                market_price=market_price,
                cost_of_equity=0.08,
                plant_lifetime=20,
                construction_time=2,
                consideration_time=3,
                probability_of_announcement=0.8,  # 80% chance, random returns 0.9 (fails)
                all_opex_subsidies=[],
                technology_emission_factors=technology_emission_factors,
                chosen_emissions_boundary_for_carbon_costs="scope_1",
                dynamic_business_cases=dynamic_business_cases,
                carbon_costs_for_iso3=carbon_costs,
            )

    # Verify - should not announce
    assert command is None
    assert len(fg.historical_npv_business_opportunities) == 3


def test_track_business_opportunity_discard_after_negative_npvs(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test that a business opportunity is discarded after consideration_time years of negative NPVs."""
    fg = get_furnace_group(
        fg_id="fg_discard",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Initialize with 2 years of negative NPV
    fg.historical_npv_business_opportunities = {
        Year(2025): -500.0,
        Year(2026): -300.0,
    }

    # Mock calculate_npv_full to return negative NPV
    with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=-400.0):
        command = fg.track_business_opportunities(
            year=Year(2027),
            location=mock_location,
            market_price=market_price,
            cost_of_equity=0.08,
            plant_lifetime=20,
            construction_time=2,
            consideration_time=3,
            probability_of_announcement=0.8,
            all_opex_subsidies=[],
            technology_emission_factors=technology_emission_factors,
            chosen_emissions_boundary_for_carbon_costs="scope_1",
            dynamic_business_cases=dynamic_business_cases,
            carbon_costs_for_iso3=carbon_costs,
        )

    # Verify
    assert command is not None
    assert isinstance(command, UpdateFurnaceGroupStatus)
    assert command.new_status == "discarded"
    assert len(fg.historical_npv_business_opportunities) == 3  # Consideration time reached
    assert fg.historical_npv_business_opportunities[Year(2027)] == -400.0


def test_track_business_opportunity_mixed_npvs_no_decision(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """
    Test that no decision is made when NPVs are mixed (some positive, some negative).
    Requires consideration_time years of consistent sign to make a decision.
    """
    fg = get_furnace_group(
        fg_id="fg_mixed",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Initialize with mixed NPVs
    fg.historical_npv_business_opportunities = {
        Year(2025): 500.0,  # Positive
        Year(2026): -200.0,  # Negative
    }

    # Mock calculate_npv_full to return positive NPV
    with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=300.0):
        command = fg.track_business_opportunities(
            year=Year(2027),
            location=mock_location,
            market_price=market_price,
            cost_of_equity=0.08,
            plant_lifetime=20,
            construction_time=2,
            consideration_time=3,
            probability_of_announcement=0.8,
            all_opex_subsidies=[],
            technology_emission_factors=technology_emission_factors,
            chosen_emissions_boundary_for_carbon_costs="scope_1",
            dynamic_business_cases=dynamic_business_cases,
            carbon_costs_for_iso3=carbon_costs,
        )

    # Verify - no decision made
    assert command is None
    assert len(fg.historical_npv_business_opportunities) == 3


def test_track_business_opportunity_insufficient_data(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test that no decision is made when there's insufficient historical data."""
    fg = get_furnace_group(
        fg_id="fg_insufficient",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Initialize with only 1 year (need 3 for consideration_time=3)
    fg.historical_npv_business_opportunities = {
        Year(2025): 1000.0,
    }

    # Mock calculate_npv_full to return positive NPV
    with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=1200.0):
        command = fg.track_business_opportunities(
            year=Year(2026),
            location=mock_location,
            market_price=market_price,
            cost_of_equity=0.08,
            plant_lifetime=20,
            construction_time=2,
            consideration_time=3,
            probability_of_announcement=0.8,
            all_opex_subsidies=[],
            technology_emission_factors=technology_emission_factors,
            chosen_emissions_boundary_for_carbon_costs="scope_1",
            dynamic_business_cases=dynamic_business_cases,
            carbon_costs_for_iso3=carbon_costs,
        )

    # Verify - not enough data yet (has 2 years, needs 3)
    assert command is None
    assert len(fg.historical_npv_business_opportunities) == 2


def test_track_business_opportunity_missing_capex(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test that NPV is set to -inf when CAPEX is None."""
    fg = get_furnace_group(
        fg_id="fg_no_capex",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = None  # Missing CAPEX
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Initialize with 2 years of data
    fg.historical_npv_business_opportunities = {
        Year(2025): float("-inf"),
        Year(2026): float("-inf"),
    }

    command = fg.track_business_opportunities(
        year=Year(2027),
        location=mock_location,
        market_price=market_price,
        cost_of_equity=0.08,
        plant_lifetime=20,
        construction_time=2,
        consideration_time=3,
        probability_of_announcement=0.8,
        all_opex_subsidies=[],
        technology_emission_factors=technology_emission_factors,
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        dynamic_business_cases=dynamic_business_cases,
        carbon_costs_for_iso3=carbon_costs,
    )

    # Verify - should discard due to negative NPVs
    assert command is not None
    assert isinstance(command, UpdateFurnaceGroupStatus)
    assert command.new_status == "discarded"
    assert fg.historical_npv_business_opportunities[Year(2027)] == float("-inf")


def test_track_business_opportunity_missing_bom(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test that NPV is set to -inf when bill_of_materials is None."""
    fg = get_furnace_group(
        fg_id="fg_no_bom",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"
    fg.bill_of_materials = None  # Missing BOM

    # Initialize with 2 years of data
    fg.historical_npv_business_opportunities = {
        Year(2025): float("-inf"),
        Year(2026): float("-inf"),
    }

    command = fg.track_business_opportunities(
        year=Year(2027),
        location=mock_location,
        market_price=market_price,
        cost_of_equity=0.08,
        plant_lifetime=20,
        construction_time=2,
        consideration_time=3,
        probability_of_announcement=0.8,
        all_opex_subsidies=[],
        technology_emission_factors=technology_emission_factors,
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        dynamic_business_cases=dynamic_business_cases,
        carbon_costs_for_iso3=carbon_costs,
    )

    # Verify - should discard due to negative NPVs
    assert command is not None
    assert isinstance(command, UpdateFurnaceGroupStatus)
    assert command.new_status == "discarded"
    assert fg.historical_npv_business_opportunities[Year(2027)] == float("-inf")


def test_track_business_opportunity_nan_npv(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test that NaN NPV is converted to -inf."""
    fg = get_furnace_group(
        fg_id="fg_nan",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Initialize with 2 years of data
    fg.historical_npv_business_opportunities = {
        Year(2025): float("-inf"),
        Year(2026): float("-inf"),
    }

    # Mock calculate_npv_full to return NaN
    with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=float("nan")):
        command = fg.track_business_opportunities(
            year=Year(2027),
            location=mock_location,
            market_price=market_price,
            cost_of_equity=0.08,
            plant_lifetime=20,
            construction_time=2,
            consideration_time=3,
            probability_of_announcement=0.8,
            all_opex_subsidies=[],
            technology_emission_factors=technology_emission_factors,
            chosen_emissions_boundary_for_carbon_costs="scope_1",
            dynamic_business_cases=dynamic_business_cases,
            carbon_costs_for_iso3=carbon_costs,
        )

    # Verify - NaN should be converted to -inf
    assert command is not None
    assert isinstance(command, UpdateFurnaceGroupStatus)
    assert command.new_status == "discarded"
    assert fg.historical_npv_business_opportunities[Year(2027)] == float("-inf")


def test_track_business_opportunity_with_opex_subsidies(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test NPV calculation with OPEX subsidies."""
    fg = get_furnace_group(
        fg_id="fg_opex_subsidy",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Initialize with 2 years of positive NPV
    fg.historical_npv_business_opportunities = {
        Year(2025): 800.0,
        Year(2026): 900.0,
    }

    # Create OPEX subsidy
    opex_subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2030),
        end_year=Year(2040),
        technology_name="EAF",
        cost_item="opex",
        relative_subsidy=0.2,  # 20% reduction
    )

    # Mock calculate_npv_full to return higher NPV due to subsidies
    with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=1800.0):
        with patch("random.random", return_value=0.5):
            command = fg.track_business_opportunities(
                year=Year(2027),
                location=mock_location,
                market_price=market_price,
                cost_of_equity=0.08,
                plant_lifetime=20,
                construction_time=2,
                consideration_time=3,
                probability_of_announcement=0.8,
                all_opex_subsidies=[opex_subsidy],
                technology_emission_factors=technology_emission_factors,
                chosen_emissions_boundary_for_carbon_costs="scope_1",
                dynamic_business_cases=dynamic_business_cases,
                carbon_costs_for_iso3=carbon_costs,
            )

    # Verify - should announce due to positive NPVs
    assert command is not None
    assert isinstance(command, UpdateFurnaceGroupStatus)
    assert command.new_status == "announced"
    assert fg.historical_npv_business_opportunities[Year(2027)] == 1800.0


def test_track_business_opportunity_error_on_missing_previous_year(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test that an error is raised if previous year NPV is missing."""
    fg = get_furnace_group(
        fg_id="fg_missing_prev",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Initialize with empty historical NPVs (should trigger error)
    fg.historical_npv_business_opportunities = {}

    # Mock calculate_npv_full
    with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=1000.0):
        with pytest.raises(ValueError, match="No historical NPV found"):
            fg.track_business_opportunities(
                year=Year(2027),
                location=mock_location,
                market_price=market_price,
                cost_of_equity=0.08,
                plant_lifetime=20,
                construction_time=2,
                consideration_time=3,
                probability_of_announcement=0.8,
                all_opex_subsidies=[],
                technology_emission_factors=technology_emission_factors,
                chosen_emissions_boundary_for_carbon_costs="scope_1",
                dynamic_business_cases=dynamic_business_cases,
                carbon_costs_for_iso3=carbon_costs,
            )


def test_track_business_opportunity_initializes_historical_npvs(
    mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
):
    """Test that historical_npv_business_opportunities is initialized if None."""
    fg = get_furnace_group(
        fg_id="fg_init",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0  # Will be divided by utilization_rate (0.7) to give ~50.0
    fg.equity_share = 0.3
    fg.railway_cost = 0.0
    fg.chosen_reductant = "scrap"

    # Set to None
    fg.historical_npv_business_opportunities = None

    # Mock calculate_npv_full
    with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=1000.0):
        # This should raise ValueError because no previous year exists
        with pytest.raises(ValueError, match="No historical NPV found"):
            fg.track_business_opportunities(
                year=Year(2027),
                location=mock_location,
                market_price=market_price,
                cost_of_equity=0.08,
                plant_lifetime=20,
                construction_time=2,
                consideration_time=3,
                probability_of_announcement=0.8,
                all_opex_subsidies=[],
                technology_emission_factors=technology_emission_factors,
                chosen_emissions_boundary_for_carbon_costs="scope_1",
                dynamic_business_cases=dynamic_business_cases,
                carbon_costs_for_iso3=carbon_costs,
            )

    # Verify it was initialized
    assert fg.historical_npv_business_opportunities is not None
    assert isinstance(fg.historical_npv_business_opportunities, dict)
