"""Tests for new plant opening methods - updating status of business opportunities."""

import pytest
from unittest.mock import patch
from steelo.domain.models import Subsidy, Location, TechnologyEmissionFactors, PrimaryFeedstock, PlantGroup
from steelo.domain.commands import UpdateFurnaceGroupStatus
from steelo.devdata import get_furnace_group, get_plant, PointInTime, TimeFrame, Year


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


@pytest.fixture
def capex_dict_all_locs():
    """CAPEX by region and technology."""
    return {
        "Americas": {
            "EAF": 1000.0,
            "BOF": 1500.0,
            "DRI": 2000.0,
            "DRIH2": 2500.0,
        },
        "Europe": {
            "EAF": 1100.0,
            "BOF": 1600.0,
            "DRI": 2100.0,
            "DRIH2": 2600.0,
        },
    }


@pytest.fixture
def cost_debt_all_locs():
    """Cost of debt by ISO3 country code."""
    return {
        "USA": 0.05,
        "DEU": 0.04,
        "CHN": 0.06,
    }


@pytest.fixture
def iso3_to_region_map():
    """Map ISO3 codes to regions."""
    return {
        "USA": "Americas",
        "DEU": "Europe",
        "CHN": "Asia",
    }


class TestTrackBusinessOpportunities:
    """Tests for FurnaceGroup.track_business_opportunities method."""

    def test_announce_after_positive_npvs(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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

    def test_not_announce_due_to_probability(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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

    def test_discard_after_negative_npvs(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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
        assert len(fg.historical_npv_business_opportunities) == 3
        assert fg.historical_npv_business_opportunities[Year(2027)] == -400.0

    def test_mixed_npvs_no_decision(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
    ):
        """Test that no decision is made when NPVs are mixed (some positive, some negative)."""
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

    def test_insufficient_data(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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

        # Verify - not enough data yet
        assert command is None
        assert len(fg.historical_npv_business_opportunities) == 2

    def test_missing_capex(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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

    def test_missing_bom(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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

    def test_nan_npv(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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

    def test_with_opex_subsidies(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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

    def test_error_on_missing_previous_year(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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

    def test_initializes_historical_npvs(
        self, mock_location, market_price, carbon_costs, technology_emission_factors, dynamic_business_cases
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


class TestConvertBusinessOpportunityIntoActualProject:
    """Tests for FurnaceGroup.convert_business_opportunity_into_actual_project method."""

    def test_discard_if_technology_not_allowed(self, mock_location):
        """Test that business opportunity is discarded if technology is no longer allowed."""
        fg = get_furnace_group(
            fg_id="fg_not_allowed",
            tech_name="EAF",
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"

        command = fg.convert_business_opportunity_into_actual_project(
            probability_of_construction=0.8,
            allowed_techs_current_year=["BOF", "DRI"],  # EAF not allowed
            new_plant_capacity_in_year=lambda product: 0.0,
            expanded_capacity=100.0,
            capacity_limit_iron=10000.0,
            capacity_limit_steel=10000.0,
            new_capacity_share_from_new_plants=0.5,
            location=mock_location,
        )

        # Verify - should be discarded
        assert command is not None
        assert isinstance(command, UpdateFurnaceGroupStatus)
        assert command.new_status == "discarded"

    def test_blocked_if_capacity_limit_reached_steel(self, mock_location):
        """Test that construction is blocked if steel capacity limit is reached."""
        fg = get_furnace_group(
            fg_id="fg_blocked_steel",
            tech_name="EAF",  # Steel product
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"

        # Capacity limit for steel: 10000 * 0.5 = 5000
        # Already used: 4950
        # Trying to add: 100
        # Total would be: 5050 > 5000 (BLOCKED)
        command = fg.convert_business_opportunity_into_actual_project(
            probability_of_construction=0.8,
            allowed_techs_current_year=["EAF", "BOF", "DRI"],
            new_plant_capacity_in_year=lambda product: 4950.0 if product == "steel" else 0.0,
            expanded_capacity=100.0,
            capacity_limit_iron=10000.0,
            capacity_limit_steel=10000.0,
            new_capacity_share_from_new_plants=0.5,
            location=mock_location,
        )

        # Verify - should stay announced (blocked)
        assert command is None

    def test_blocked_if_capacity_limit_reached_iron(self, mock_location):
        """Test that construction is blocked if iron capacity limit is reached."""
        fg = get_furnace_group(
            fg_id="fg_blocked_iron",
            tech_name="DRI",  # Iron product
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"

        # Capacity limit for iron: 8000 * 0.4 = 3200
        # Already used: 3150
        # Trying to add: 100
        # Total would be: 3250 > 3200 (BLOCKED)
        command = fg.convert_business_opportunity_into_actual_project(
            probability_of_construction=0.8,
            allowed_techs_current_year=["EAF", "BOF", "DRI"],
            new_plant_capacity_in_year=lambda product: 3150.0 if product == "iron" else 0.0,
            expanded_capacity=100.0,
            capacity_limit_iron=8000.0,
            capacity_limit_steel=10000.0,
            new_capacity_share_from_new_plants=0.4,
            location=mock_location,
        )

        # Verify - should stay announced (blocked)
        assert command is None

    def test_stay_announced_if_probability_fails(self, mock_location):
        """Test that business opportunity stays announced if probability check fails."""
        fg = get_furnace_group(
            fg_id="fg_prob_fail",
            tech_name="EAF",
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"

        # Mock random to fail probability check
        with patch("random.random", return_value=0.9):  # Returns 0.9 > 0.8 (probability)
            command = fg.convert_business_opportunity_into_actual_project(
                probability_of_construction=0.8,
                allowed_techs_current_year=["EAF", "BOF", "DRI"],
                new_plant_capacity_in_year=lambda product: 0.0,
                expanded_capacity=100.0,
                capacity_limit_iron=10000.0,
                capacity_limit_steel=10000.0,
                new_capacity_share_from_new_plants=0.5,
                location=mock_location,
            )

        # Verify - should stay announced
        assert command is None

    def test_start_construction_if_all_conditions_met(self, mock_location):
        """Test that construction starts if all conditions are met."""
        fg = get_furnace_group(
            fg_id="fg_construct",
            tech_name="EAF",
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"

        # Mock random to pass probability check
        with patch("random.random", return_value=0.5):  # Returns 0.5 <= 0.8 (pass)
            command = fg.convert_business_opportunity_into_actual_project(
                probability_of_construction=0.8,
                allowed_techs_current_year=["EAF", "BOF", "DRI"],
                new_plant_capacity_in_year=lambda product: 100.0,
                expanded_capacity=100.0,
                capacity_limit_iron=10000.0,
                capacity_limit_steel=10000.0,
                new_capacity_share_from_new_plants=0.5,
                location=mock_location,
            )

        # Verify - should start construction
        assert command is not None
        assert isinstance(command, UpdateFurnaceGroupStatus)
        assert command.new_status == "construction"

    def test_error_on_unknown_product_type(self, mock_location):
        """Test that ValueError is raised for unknown product type."""
        fg = get_furnace_group(
            fg_id="fg_unknown",
            tech_name="EAF",
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"
        # Change product to something invalid
        fg.technology.product = "unknown_product"

        with pytest.raises(ValueError, match="Unknown product type"):
            fg.convert_business_opportunity_into_actual_project(
                probability_of_construction=0.8,
                allowed_techs_current_year=["EAF", "BOF", "DRI"],
                new_plant_capacity_in_year=lambda product: 0.0,
                expanded_capacity=100.0,
                capacity_limit_iron=10000.0,
                capacity_limit_steel=10000.0,
                new_capacity_share_from_new_plants=0.5,
                location=mock_location,
            )

    def test_capacity_limit_calculation_steel(self, mock_location):
        """Test correct capacity limit calculation for steel products."""
        fg = get_furnace_group(
            fg_id="fg_steel_limit",
            tech_name="EAF",  # Steel product
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"

        # Capacity limit for steel: 10000 * 0.3 = 3000
        # Already used: 2900
        # Trying to add: 100
        # Total: 3000 (exactly at limit - should pass)
        with patch("random.random", return_value=0.5):
            command = fg.convert_business_opportunity_into_actual_project(
                probability_of_construction=0.8,
                allowed_techs_current_year=["EAF", "BOF", "DRI"],
                new_plant_capacity_in_year=lambda product: 2900.0 if product == "steel" else 0.0,
                expanded_capacity=100.0,
                capacity_limit_iron=10000.0,
                capacity_limit_steel=10000.0,
                new_capacity_share_from_new_plants=0.3,
                location=mock_location,
            )

        # Verify - should start construction (exactly at limit)
        assert command is not None
        assert isinstance(command, UpdateFurnaceGroupStatus)
        assert command.new_status == "construction"

    def test_capacity_limit_calculation_iron(self, mock_location):
        """Test correct capacity limit calculation for iron products."""
        fg = get_furnace_group(
            fg_id="fg_iron_limit",
            tech_name="DRI",  # Iron product
            capacity=50,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"

        # Capacity limit for iron: 5000 * 0.6 = 3000
        # Already used: 2950
        # Trying to add: 50
        # Total: 3000 (exactly at limit - should pass)
        with patch("random.random", return_value=0.5):
            command = fg.convert_business_opportunity_into_actual_project(
                probability_of_construction=0.8,
                allowed_techs_current_year=["EAF", "BOF", "DRI"],
                new_plant_capacity_in_year=lambda product: 2950.0 if product == "iron" else 0.0,
                expanded_capacity=50.0,
                capacity_limit_iron=5000.0,
                capacity_limit_steel=10000.0,
                new_capacity_share_from_new_plants=0.6,
                location=mock_location,
            )

        # Verify - should start construction (exactly at limit)
        assert command is not None
        assert isinstance(command, UpdateFurnaceGroupStatus)
        assert command.new_status == "construction"


class TestUpdateStatusOfBusinessOpportunities:
    """Tests for PlantGroup.update_status_of_business_opportunities method."""

    def test_processes_announced_plants(
        self,
        market_price,
        carbon_costs,
        technology_emission_factors,
        dynamic_business_cases,
    ):
        """Test that announced plants are processed through convert_business_opportunity_into_actual_project."""
        # Create plant with announced furnace group
        fg = get_furnace_group(
            fg_id="fg_announced",
            tech_name="EAF",
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"

        plant = get_plant(plant_id="plant_announced", furnace_groups=[fg])
        plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")
        plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

        # Mock random to pass probability check
        with patch("random.random", return_value=0.5):
            commands = plant_group.update_status_of_business_opportunities(
                current_year=Year(2027),
                allowed_techs={Year(2027): ["EAF", "BOF", "DRI"]},
                dynamic_business_cases=dynamic_business_cases,
                technology_emission_factors=technology_emission_factors,
                market_price=market_price,
                cost_of_equity_all_locs={"USA": 0.08},
                plant_lifetime=20,
                construction_time=2,
                consideration_time=3,
                probability_of_announcement=0.8,
                probability_of_construction=0.8,
                opex_subsidies={},
                chosen_emissions_boundary_for_carbon_costs="scope_1",
                carbon_costs={"USA": carbon_costs},
                new_plant_capacity_in_year=lambda product: 0.0,
                expanded_capacity=100.0,
                capacity_limit_iron=10000.0,
                capacity_limit_steel=10000.0,
                new_capacity_share_from_new_plants=0.5,
            )

        # Verify - should have command to update status to construction
        assert len(commands) == 1
        assert isinstance(commands[0], UpdateFurnaceGroupStatus)
        assert commands[0].new_status == "construction"

    def test_processes_considered_plants(
        self,
        market_price,
        carbon_costs,
        technology_emission_factors,
        dynamic_business_cases,
    ):
        """Test that considered plants are processed through track_business_opportunities."""
        # Create plant with considered furnace group
        fg = get_furnace_group(
            fg_id="fg_considered",
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
        fg.tech_unit_fopex = 35.0
        fg.equity_share = 0.3
        fg.railway_cost = 0.0
        fg.chosen_reductant = "scrap"
        fg.historical_npv_business_opportunities = {Year(2025): 800.0, Year(2026): 900.0}

        plant = get_plant(plant_id="plant_considered", furnace_groups=[fg])
        plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")
        plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

        # Mock NPV calculation and random check
        with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=1200.0):
            with patch("random.random", return_value=0.5):
                commands = plant_group.update_status_of_business_opportunities(
                    current_year=Year(2027),
                    allowed_techs={Year(2027): ["EAF", "BOF", "DRI"]},
                    dynamic_business_cases=dynamic_business_cases,
                    technology_emission_factors=technology_emission_factors,
                    market_price=market_price,
                    cost_of_equity_all_locs={"USA": 0.08},
                    plant_lifetime=20,
                    construction_time=2,
                    consideration_time=3,
                    probability_of_announcement=0.8,
                    probability_of_construction=0.8,
                    opex_subsidies={},
                    chosen_emissions_boundary_for_carbon_costs="scope_1",
                    carbon_costs={"USA": carbon_costs},
                    new_plant_capacity_in_year=lambda product: 0.0,
                    expanded_capacity=100.0,
                    capacity_limit_iron=10000.0,
                    capacity_limit_steel=10000.0,
                    new_capacity_share_from_new_plants=0.5,
                )

        # Verify - should have command to update status to announced
        assert len(commands) == 1
        assert isinstance(commands[0], UpdateFurnaceGroupStatus)
        assert commands[0].new_status == "announced"

    def test_processes_both_announced_and_considered(
        self,
        market_price,
        carbon_costs,
        technology_emission_factors,
        dynamic_business_cases,
    ):
        """Test that both announced and considered furnace groups in the same plant are processed."""
        # Create announced furnace group
        fg_announced = get_furnace_group(
            fg_id="fg_announced",
            tech_name="EAF",
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg_announced.status = "announced"

        # Create considered furnace group
        fg_considered = get_furnace_group(
            fg_id="fg_considered",
            tech_name="BOF",
            capacity=200,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg_considered.status = "considered"
        fg_considered.cost_of_debt = 0.05
        fg_considered.technology.capex = 1000.0
        fg_considered.tech_unit_fopex = 35.0
        fg_considered.equity_share = 0.3
        fg_considered.railway_cost = 0.0
        fg_considered.chosen_reductant = "scrap"
        fg_considered.historical_npv_business_opportunities = {Year(2025): 800.0, Year(2026): 900.0}

        plant = get_plant(plant_id="plant_both", furnace_groups=[fg_announced, fg_considered])
        plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")
        plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

        # Mock NPV calculation and random check
        with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=1200.0):
            with patch("random.random", return_value=0.5):
                commands = plant_group.update_status_of_business_opportunities(
                    current_year=Year(2027),
                    allowed_techs={Year(2027): ["EAF", "BOF", "DRI"]},
                    dynamic_business_cases=dynamic_business_cases,
                    technology_emission_factors=technology_emission_factors,
                    market_price=market_price,
                    cost_of_equity_all_locs={"USA": 0.08},
                    plant_lifetime=20,
                    construction_time=2,
                    consideration_time=3,
                    probability_of_announcement=0.8,
                    probability_of_construction=0.8,
                    opex_subsidies={},
                    chosen_emissions_boundary_for_carbon_costs="scope_1",
                    carbon_costs={"USA": carbon_costs},
                    new_plant_capacity_in_year=lambda product: 0.0,
                    expanded_capacity=100.0,
                    capacity_limit_iron=10000.0,
                    capacity_limit_steel=10000.0,
                    new_capacity_share_from_new_plants=0.5,
                )

        # Verify - should have 2 commands
        assert len(commands) == 2
        statuses = {cmd.new_status for cmd in commands}
        assert "construction" in statuses  # announced -> construction
        assert "announced" in statuses  # considered -> announced

    def test_skips_operating_plants(
        self,
        market_price,
        carbon_costs,
        technology_emission_factors,
        dynamic_business_cases,
    ):
        """Test that operating plants are skipped."""
        fg = get_furnace_group(
            fg_id="fg_operating",
            tech_name="EAF",
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "operating"

        plant = get_plant(plant_id="plant_operating", furnace_groups=[fg])
        plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")
        plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

        commands = plant_group.update_status_of_business_opportunities(
            current_year=Year(2027),
            allowed_techs={Year(2027): ["EAF", "BOF", "DRI"]},
            dynamic_business_cases=dynamic_business_cases,
            technology_emission_factors=technology_emission_factors,
            market_price=market_price,
            cost_of_equity_all_locs={"USA": 0.08},
            plant_lifetime=20,
            construction_time=2,
            consideration_time=3,
            probability_of_announcement=0.8,
            probability_of_construction=0.8,
            opex_subsidies={},
            chosen_emissions_boundary_for_carbon_costs="scope_1",
            carbon_costs={"USA": carbon_costs},
            new_plant_capacity_in_year=lambda product: 0.0,
            expanded_capacity=100.0,
            capacity_limit_iron=10000.0,
            capacity_limit_steel=10000.0,
            new_capacity_share_from_new_plants=0.5,
        )

        # Verify - no commands should be generated
        assert len(commands) == 0

    def test_error_on_missing_allowed_techs(
        self,
        market_price,
        carbon_costs,
        technology_emission_factors,
        dynamic_business_cases,
    ):
        """Test that ValueError is raised if allowed_techs for current_year is missing."""
        fg = get_furnace_group(
            fg_id="fg_test",
            tech_name="EAF",
            capacity=100,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
                plant_lifetime=20,
            ),
            utilization_rate=0.7,
        )
        fg.status = "announced"

        plant = get_plant(plant_id="plant_test", furnace_groups=[fg])
        plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")
        plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

        with pytest.raises(ValueError, match="No allowed technologies found for year"):
            plant_group.update_status_of_business_opportunities(
                current_year=Year(2027),
                allowed_techs={Year(2026): ["EAF", "BOF", "DRI"]},  # Missing Year(2027)
                dynamic_business_cases=dynamic_business_cases,
                technology_emission_factors=technology_emission_factors,
                market_price=market_price,
                cost_of_equity_all_locs={"USA": 0.08},
                plant_lifetime=20,
                construction_time=2,
                consideration_time=3,
                probability_of_announcement=0.8,
                probability_of_construction=0.8,
                opex_subsidies={},
                chosen_emissions_boundary_for_carbon_costs="scope_1",
                carbon_costs={"USA": carbon_costs},
                new_plant_capacity_in_year=lambda product: 0.0,
                expanded_capacity=100.0,
                capacity_limit_iron=10000.0,
                capacity_limit_steel=10000.0,
                new_capacity_share_from_new_plants=0.5,
            )

    def test_skip_plant_with_missing_cost_of_equity(
        self,
        market_price,
        carbon_costs,
        technology_emission_factors,
        dynamic_business_cases,
    ):
        """Test that plants with missing cost_of_equity_all_locs for ISO3 are skipped with a warning."""
        fg = get_furnace_group(
            fg_id="fg_test",
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
        fg.tech_unit_fopex = 35.0
        fg.equity_share = 0.3
        fg.railway_cost = 0.0
        fg.chosen_reductant = "scrap"
        fg.historical_npv_business_opportunities = {Year(2025): 800.0, Year(2026): 900.0}

        plant = get_plant(plant_id="plant_test", furnace_groups=[fg])
        plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")
        plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

        # cost_of_equity_all_locs missing USA - should skip with warning
        commands = plant_group.update_status_of_business_opportunities(
            current_year=Year(2027),
            allowed_techs={Year(2027): ["EAF", "BOF", "DRI"]},
            dynamic_business_cases=dynamic_business_cases,
            technology_emission_factors=technology_emission_factors,
            market_price=market_price,
            cost_of_equity_all_locs={"DEU": 0.08},  # Missing USA
            plant_lifetime=20,
            construction_time=2,
            consideration_time=3,
            probability_of_announcement=0.8,
            probability_of_construction=0.8,
            opex_subsidies={},
            chosen_emissions_boundary_for_carbon_costs="scope_1",
            carbon_costs={"USA": carbon_costs},
            new_plant_capacity_in_year=lambda product: 0.0,
            expanded_capacity=100.0,
            capacity_limit_iron=10000.0,
            capacity_limit_steel=10000.0,
            new_capacity_share_from_new_plants=0.5,
        )

        # Verify - should have no commands (plant was skipped)
        assert len(commands) == 0

    def test_error_on_missing_carbon_costs(
        self,
        market_price,
        technology_emission_factors,
        dynamic_business_cases,
    ):
        """Test that ValueError is raised if carbon_costs for ISO3 is missing."""
        fg = get_furnace_group(
            fg_id="fg_test",
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
        fg.tech_unit_fopex = 35.0
        fg.equity_share = 0.3
        fg.railway_cost = 0.0
        fg.chosen_reductant = "scrap"
        fg.historical_npv_business_opportunities = {Year(2025): 800.0, Year(2026): 900.0}

        plant = get_plant(plant_id="plant_test", furnace_groups=[fg])
        plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")
        plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

        with pytest.raises(ValueError, match="Carbon costs not found for ISO3"):
            plant_group.update_status_of_business_opportunities(
                current_year=Year(2027),
                allowed_techs={Year(2027): ["EAF", "BOF", "DRI"]},
                dynamic_business_cases=dynamic_business_cases,
                technology_emission_factors=technology_emission_factors,
                market_price=market_price,
                cost_of_equity_all_locs={"USA": 0.08},
                plant_lifetime=20,
                construction_time=2,
                consideration_time=3,
                probability_of_announcement=0.8,
                probability_of_construction=0.8,
                opex_subsidies={},
                chosen_emissions_boundary_for_carbon_costs="scope_1",
                carbon_costs={"DEU": {Year(2027): 50.0}},  # Missing USA
                new_plant_capacity_in_year=lambda product: 0.0,
                expanded_capacity=100.0,
                capacity_limit_iron=10000.0,
                capacity_limit_steel=10000.0,
                new_capacity_share_from_new_plants=0.5,
            )

    def test_with_opex_subsidies(
        self,
        market_price,
        carbon_costs,
        technology_emission_factors,
        dynamic_business_cases,
    ):
        """Test that OPEX subsidies are correctly passed through to both paths."""
        # Create considered furnace group
        fg = get_furnace_group(
            fg_id="fg_subsidy",
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
        fg.tech_unit_fopex = 35.0
        fg.equity_share = 0.3
        fg.railway_cost = 0.0
        fg.chosen_reductant = "scrap"
        fg.historical_npv_business_opportunities = {Year(2025): 800.0, Year(2026): 900.0}

        plant = get_plant(plant_id="plant_subsidy", furnace_groups=[fg])
        plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")
        plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

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

        # Mock NPV calculation with higher NPV due to subsidies
        with patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=1800.0):
            with patch("random.random", return_value=0.5):
                commands = plant_group.update_status_of_business_opportunities(
                    current_year=Year(2027),
                    allowed_techs={Year(2027): ["EAF", "BOF", "DRI"]},
                    dynamic_business_cases=dynamic_business_cases,
                    technology_emission_factors=technology_emission_factors,
                    market_price=market_price,
                    cost_of_equity_all_locs={"USA": 0.08},
                    plant_lifetime=20,
                    construction_time=2,
                    consideration_time=3,
                    probability_of_announcement=0.8,
                    probability_of_construction=0.8,
                    opex_subsidies={"USA": {"EAF": [opex_subsidy]}},
                    chosen_emissions_boundary_for_carbon_costs="scope_1",
                    carbon_costs={"USA": carbon_costs},
                    new_plant_capacity_in_year=lambda product: 0.0,
                    expanded_capacity=100.0,
                    capacity_limit_iron=10000.0,
                    capacity_limit_steel=10000.0,
                    new_capacity_share_from_new_plants=0.5,
                )

        # Verify - should announce due to positive NPVs with subsidies
        assert len(commands) == 1
        assert isinstance(commands[0], UpdateFurnaceGroupStatus)
        assert commands[0].new_status == "announced"
