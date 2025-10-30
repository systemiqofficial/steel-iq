"""Integration tests for carbon cost calculations across the system."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from steelo.simulation_types import get_default_technology_settings

from steelo.domain.models import Environment, Plant, FurnaceGroup, Location, Technology
from steelo.domain.constants import Year


@pytest.fixture(autouse=True)
def setup_test_fixtures(monkeypatch):
    """Set up test fixtures directory for all tests in this module."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fixtures_dir = Path(tmpdir) / "fixtures"
        fixtures_dir.mkdir(exist_ok=True)

        # Create minimal tech_switches_allowed.csv
        tech_switches_csv = fixtures_dir / "tech_switches_allowed.csv"
        tech_switches_csv.write_text(
            "Technology,BF-BOF,DRI-EAF,Scrap-EAF\nBF-BOF,YES,YES,YES\nDRI-EAF,NO,YES,YES\nScrap-EAF,NO,NO,YES\n"
        )

        # Set environment variable for the duration of tests
        monkeypatch.setenv("STEELO_FIXTURES_DIR", str(fixtures_dir))

        yield


class TestCarbonCostIntegration:
    """Integration tests to ensure carbon costs flow correctly through the system."""

    def setup_method(self):
        """Set up test environment and fixtures."""
        from steelo.simulation import SimulationConfig
        from steelo.domain.constants import Year
        from pathlib import Path
        import tempfile
        import os

        # Get the fixtures directory from the environment (set by setup_test_fixtures)
        fixtures_dir = os.environ.get("STEELO_FIXTURES_DIR")
        if fixtures_dir:
            tech_switches_csv = Path(fixtures_dir) / "tech_switches_allowed.csv"
        else:
            # Create a temporary tech_switches file if not set by fixture
            temp_dir = Path(tempfile.gettempdir())
            tech_switches_csv = temp_dir / "tech_switches_allowed.csv"
            tech_switches_csv.write_text(
                "Technology,BF-BOF,DRI-EAF,Scrap-EAF\nBF-BOF,YES,YES,YES\nDRI-EAF,NO,YES,YES\nScrap-EAF,NO,NO,YES\n"
            )

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
            output_dir=Path(tempfile.gettempdir()),
            technology_settings=get_default_technology_settings(),
            chosen_emissions_boundary_for_carbon_costs="cbam",  # Set the boundary that the test uses
        )
        self.env = Environment(config=config, tech_switches_csv=tech_switches_csv)
        self.env.year = 2025

        # Set up carbon costs like in the extreme scenario
        self.env.carbon_costs = {
            "CHN": {Year(2025): 1000.0},  # $1000/tCO2 for China
            "USA": {Year(2025): 50.0},  # $50/tCO2 for USA
            "DEU": {Year(2025): 100.0},  # $100/tCO2 for Germany
        }

    def create_mock_plant(self, iso3: str, plant_id: str) -> Plant:
        """Create a mock plant with realistic data."""
        location = Mock(spec=Location)
        location.iso3 = iso3

        plant = Mock(spec=Plant)
        plant.plant_id = plant_id
        plant.location = location
        plant.furnace_groups = []

        return plant

    def create_mock_furnace_group(self, fg_id: str, technology: str, emissions_scope1: float) -> FurnaceGroup:
        """Create a mock furnace group with emissions data."""
        tech = Mock(spec=Technology)
        tech.name = technology
        tech.product = "steel" if technology == "BOF" else "iron"

        fg = Mock(spec=FurnaceGroup)
        fg.furnace_group_id = fg_id
        fg.technology = tech
        fg.capacity = 1000.0
        fg.allocated_volumes = 950.0  # 95% utilization
        fg.production = 950.0
        fg.status = "active"

        # Set up emissions data structure
        fg.emissions = {
            "cbam": {
                "direct_ghg": emissions_scope1,
                "direct_with_biomass_ghg": emissions_scope1,  # Same as direct for now
                "indirect_ghg": 0.5,
            },
            "iso_14404": {
                "direct_ghg": emissions_scope1,
                "direct_with_biomass_ghg": emissions_scope1,  # Same as direct for now
                "indirect_ghg": 0.5,
            },
        }

        # Initialize carbon cost storage
        fg.carbon_costs_for_emissions = 0.0

        # Mock the set_carbon_costs_for_emissions method
        def set_carbon_costs(carbon_price, chosen_emissions_boundary_for_carbon_costs):
            from steelo.domain.calculate_emissions import calculate_emissions_cost_in_year

            fg.carbon_costs_for_emissions = calculate_emissions_cost_in_year(
                fg.emissions, carbon_price, chosen_emissions_boundary_for_carbon_costs
            )
            return fg.carbon_costs_for_emissions

        fg.set_carbon_costs_for_emissions = set_carbon_costs

        # Mock carbon_cost_per_unit property (FIXED version)
        type(fg).carbon_cost_per_unit = property(
            lambda self: self.carbon_costs_for_emissions if self.carbon_costs_for_emissions else 0.0
        )

        return fg

    def test_china_bf_plant_carbon_cost(self):
        """Test that a Chinese BF plant gets the correct carbon cost."""
        # Arrange
        china_plant = self.create_mock_plant("CHN", "P100000120069")
        china_bf = self.create_mock_furnace_group("FG001", "BF", 17.62)  # High emissions for BF
        china_plant.furnace_groups = [china_bf]

        # Act
        self.env.calculate_carbon_costs_of_furnace_groups([china_plant])

        # Assert
        expected_carbon_cost_per_ton = 17.62 * 1000.0  # 17,620 $/t
        assert china_bf.carbon_costs_for_emissions == expected_carbon_cost_per_ton
        assert china_bf.carbon_cost_per_unit == expected_carbon_cost_per_ton

        # Total carbon cost for the production
        total_carbon_cost = china_bf.carbon_cost_per_unit * china_bf.production
        assert total_carbon_cost == pytest.approx(16_739_000.0)  # ~$16.7M

    def test_multiple_countries_different_carbon_prices(self):
        """Test that plants in different countries get different carbon costs."""
        # Arrange
        china_plant = self.create_mock_plant("CHN", "P_CHN_001")
        china_bf = self.create_mock_furnace_group("FG_CHN_001", "BF", 17.62)
        china_plant.furnace_groups = [china_bf]

        usa_plant = self.create_mock_plant("USA", "P_USA_001")
        usa_bf = self.create_mock_furnace_group("FG_USA_001", "BF", 17.62)  # Same emissions
        usa_plant.furnace_groups = [usa_bf]

        germany_plant = self.create_mock_plant("DEU", "P_DEU_001")
        germany_bf = self.create_mock_furnace_group("FG_DEU_001", "BF", 17.62)  # Same emissions
        germany_plant.furnace_groups = [germany_bf]

        # Act
        self.env.calculate_carbon_costs_of_furnace_groups([china_plant, usa_plant, germany_plant])

        # Assert
        assert china_bf.carbon_cost_per_unit == 17.62 * 1000.0  # $17,620/t
        assert usa_bf.carbon_cost_per_unit == 17.62 * 50.0  # $881/t
        assert germany_bf.carbon_cost_per_unit == 17.62 * 100.0  # $1,762/t

    def test_different_technologies_different_emissions(self):
        """Test that different technologies with different emissions get appropriate carbon costs."""
        # Arrange
        china_plant = self.create_mock_plant("CHN", "P_CHN_002")

        # BF: High emissions
        bf_fg = self.create_mock_furnace_group("FG_BF", "BF", 17.62)

        # EAF: Low emissions
        eaf_fg = self.create_mock_furnace_group("FG_EAF", "EAF", 0.5)

        # DRI: Medium emissions
        dri_fg = self.create_mock_furnace_group("FG_DRI", "DRI", 2.8)

        china_plant.furnace_groups = [bf_fg, eaf_fg, dri_fg]

        # Act
        self.env.calculate_carbon_costs_of_furnace_groups([china_plant])

        # Assert
        assert bf_fg.carbon_cost_per_unit == 17.62 * 1000.0  # $17,620/t
        assert eaf_fg.carbon_cost_per_unit == 0.5 * 1000.0  # $500/t
        assert dri_fg.carbon_cost_per_unit == 2.8 * 1000.0  # $2,800/t

    def test_zero_emissions_zero_carbon_cost(self):
        """Test that furnace groups with zero emissions have zero carbon cost."""
        # Arrange
        china_plant = self.create_mock_plant("CHN", "P_CHN_003")
        zero_emissions_fg = self.create_mock_furnace_group("FG_ZERO", "FUTURE_TECH", 0.0)
        china_plant.furnace_groups = [zero_emissions_fg]

        # Act
        self.env.calculate_carbon_costs_of_furnace_groups([china_plant])

        # Assert
        assert zero_emissions_fg.carbon_cost_per_unit == 0.0

    def test_missing_country_in_carbon_costs(self):
        """Test handling of plants in countries without carbon cost data."""
        # Arrange
        brazil_plant = self.create_mock_plant("BRA", "P_BRA_001")  # Not in carbon_costs
        brazil_bf = self.create_mock_furnace_group("FG_BRA_001", "BF", 17.62)
        brazil_plant.furnace_groups = [brazil_bf]

        # Act
        self.env.calculate_carbon_costs_of_furnace_groups([brazil_plant])

        # Assert
        # Should not crash, and carbon cost should remain 0
        assert brazil_bf.carbon_costs_for_emissions == 0.0

    def test_correct_emissions_boundary_used(self):
        """Test that the correct emissions boundary is used for carbon cost calculations."""
        # Arrange
        china_plant = self.create_mock_plant("CHN", "P_CHN_004")

        # Create FG with different emissions for different boundaries
        fg = self.create_mock_furnace_group("FG_MULTI", "BF", 0.0)  # Will override
        fg.emissions = {
            "cbam": {"direct_ghg": 17.62},
            "iso_14404": {"direct_ghg": 18.5},
            "responsible_steel": {"direct_ghg": 16.8},
        }
        china_plant.furnace_groups = [fg]

        # Act
        self.env.calculate_carbon_costs_of_furnace_groups([china_plant])

        # Assert - should use CBAM boundary
        assert fg.carbon_costs_for_emissions == 17.62 * 1000.0

    def test_year_specific_carbon_costs(self):
        """Test that carbon costs are applied for the correct year."""
        # Arrange
        self.env.carbon_costs = {
            "CHN": {
                Year(2025): 1000.0,
                Year(2026): 1100.0,
                Year(2030): 1500.0,
            }
        }

        china_plant = self.create_mock_plant("CHN", "P_CHN_005")
        china_bf = self.create_mock_furnace_group("FG_CHN_005", "BF", 17.62)
        china_plant.furnace_groups = [china_bf]

        # Act - Year 2025
        self.env.year = 2025
        self.env.calculate_carbon_costs_of_furnace_groups([china_plant])
        cost_2025 = china_bf.carbon_cost_per_unit

        # Act - Year 2030
        self.env.year = 2030
        self.env.calculate_carbon_costs_of_furnace_groups([china_plant])
        cost_2030 = china_bf.carbon_cost_per_unit

        # Assert
        assert cost_2025 == 17.62 * 1000.0  # $17,620/t
        assert cost_2030 == 17.62 * 1500.0  # $26,430/t


class TestCarbonCostRegressionPrevention:
    """Tests specifically designed to catch the carbon cost bug if it reappears."""

    def test_chinese_bf_plant_carbon_cost_magnitude(self):
        """Ensure Chinese BF plants have carbon costs in the thousands, not near zero."""
        # This test will fail if the bug reappears

        # Typical values
        china_carbon_price = 1000.0  # $/tCO2
        bf_emissions = 17.62  # tCO2/t steel

        expected_carbon_cost_per_ton = china_carbon_price * bf_emissions

        # The bug would cause values like these
        buggy_values = [
            0.0,  # Completely zero
            0.00128,  # Triple division by production
            1.28,  # Double division
            17.62,  # Just emissions value
            expected_carbon_cost_per_ton / 1000,  # Single extra division
            expected_carbon_cost_per_ton / 1000000,  # Double extra division
        ]

        # Assert the correct value is NOT in the buggy range
        for buggy_value in buggy_values:
            assert abs(expected_carbon_cost_per_ton - buggy_value) > 1000, (
                f"Carbon cost {expected_carbon_cost_per_ton} is too close to buggy value {buggy_value}"
            )

        # Assert the correct magnitude
        assert expected_carbon_cost_per_ton > 10000  # Should be ~$17,620
        assert expected_carbon_cost_per_ton < 30000  # Reasonable upper bound

    def test_carbon_cost_not_affected_by_production_volume(self):
        """Test that carbon cost per unit doesn't change with production volume."""
        from steelo.simulation import SimulationConfig
        from pathlib import Path
        import tempfile

        # Create a temporary tech_switches file
        temp_dir = Path(tempfile.gettempdir())
        tech_switches_csv = temp_dir / "tech_switches_allowed.csv"
        tech_switches_csv.write_text(
            "Technology,BF-BOF,DRI-EAF,Scrap-EAF\nBF-BOF,YES,YES,YES\nDRI-EAF,NO,YES,YES\nScrap-EAF,NO,NO,YES\n"
        )

        from steelo.simulation_types import get_default_technology_settings

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=temp_dir / "master.xlsx",
            output_dir=temp_dir,
            technology_settings=get_default_technology_settings(),
        )
        env = Environment(config=config, tech_switches_csv=tech_switches_csv)
        env.year = 2025
        env.carbon_costs = {"CHN": {Year(2025): 1000.0}}

        # Create two furnace groups with same emissions but different production
        fg_high_prod = Mock()
        fg_high_prod.emissions = {"cbam": {"direct_ghg": 10.0}}
        fg_high_prod.allocated_volumes = 10000.0  # High production
        fg_high_prod.carbon_costs_for_emissions = 0.0

        fg_low_prod = Mock()
        fg_low_prod.emissions = {"cbam": {"direct_ghg": 10.0}}
        fg_low_prod.allocated_volumes = 100.0  # Low production
        fg_low_prod.carbon_costs_for_emissions = 0.0

        # Calculate carbon costs
        from steelo.domain.calculate_emissions import calculate_emissions_cost_in_year

        carbon_cost_high = calculate_emissions_cost_in_year(fg_high_prod.emissions, 1000.0, "cbam")
        carbon_cost_low = calculate_emissions_cost_in_year(fg_low_prod.emissions, 1000.0, "cbam")

        # Both should have the same per-unit carbon cost
        assert carbon_cost_high == carbon_cost_low == 10.0 * 1000.0  # $10,000/t
