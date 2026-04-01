"""
Unit tests for green steel grades functionality.

Tests the green steel grading system including:
- GreenSteelGrade threshold calculations
- FurnaceGroup emissions intensity calculations
- FurnaceGroup scrap share calculations
- Grade determination logic
"""

import pytest
from datetime import date
from pathlib import Path
import tempfile
import pandas as pd

from steelo.domain.models import (
    GreenSteelGrade,
    FurnaceGroup,
    Environment,
    Technology,
    PrimaryFeedstock,
    PointInTime,
    TimeFrame,
)
from steelo.domain import Volumes, Year
from steelo.simulation import SimulationConfig
from steelo.simulation_types import TechnologySettings
from steelo.adapters.dataprocessing.excel_reader import read_green_steel_definitions


@pytest.fixture
def tech_switches_csv(tmp_path):
    """Create a temporary tech_switches_allowed.csv file for testing."""
    csv_content = """Tech,BF,BOF,DRI,EAF
BF,NO,NO,NO,NO
BOF,NO,NO,YES,YES
DRI,NO,NO,NO,NO
EAF,NO,NO,NO,NO"""

    csv_path = tmp_path / "tech_switches_allowed.csv"
    csv_path.write_text(csv_content)
    return csv_path


def create_test_config(tmp_path):
    """Helper to create a minimal SimulationConfig for tests."""
    default_tech_settings = {
        "BF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "BOF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRI": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "EAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
    }

    return SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=tmp_path / "master.xlsx",
        output_dir=tmp_path,
        technology_settings=default_tech_settings,
        chosen_emissions_boundary_for_carbon_costs="responsible_steel",
    )


class TestGreenSteelGrade:
    """Test the GreenSteelGrade dataclass and threshold checking."""

    def test_green_steel_grade_creation(self):
        """Test creating a GreenSteelGrade object."""
        grade = GreenSteelGrade(
            level=1,
            name="Level 1",
            b=0.4,  # y-intercept
            m=0.005,  # slope
        )

        assert grade.level == 1
        assert grade.name == "Level 1"
        assert grade.b == 0.4
        assert grade.m == 0.005

    def test_check_threshold_pass(self):
        """Test that emissions below threshold pass."""
        grade = GreenSteelGrade(
            level=1,
            name="Level 1",
            b=0.4,
            m=0.5,  # Adjusted for fraction calculation
        )

        # With 50% scrap share: threshold = 0.4 - 0.5 * 0.5 = 0.15
        # Emissions of 0.1 should pass
        assert grade.check_threshold(emissions_intensity=0.1, scrap_share=50) is True

    def test_check_threshold_fail(self):
        """Test that emissions above threshold fail."""
        grade = GreenSteelGrade(
            level=1,
            name="Level 1",
            b=0.4,
            m=0.5,  # Adjusted for fraction calculation
        )

        # With 50% scrap share: threshold = 0.4 - 0.5 * 0.5 = 0.15
        # Emissions of 0.2 should fail
        assert grade.check_threshold(emissions_intensity=0.2, scrap_share=50) is False

    def test_check_threshold_boundary(self):
        """Test threshold at exact boundary."""
        grade = GreenSteelGrade(
            level=2,
            name="Level 2",
            b=0.7,
            m=0.3,  # Adjusted for fraction calculation
        )

        # With 100% scrap share: threshold = 0.7 - 0.3 * 1.0 = 0.4
        # Emissions of exactly 0.4 should pass (<=)
        # Note: Due to floating point precision, we need to be careful with exact comparisons
        threshold = 0.7 - 0.3 * 1.0  # = 0.4
        assert abs(threshold - 0.4) < 1e-10  # Verify our math

        # Test with slightly below and above threshold
        assert grade.check_threshold(emissions_intensity=0.399999, scrap_share=100) is True
        assert grade.check_threshold(emissions_intensity=0.400001, scrap_share=100) is False

        # Exact threshold should pass (<=)
        assert grade.check_threshold(emissions_intensity=threshold, scrap_share=100) is True


class TestExcelReader:
    """Test reading green steel definitions from Excel."""

    def test_read_green_steel_definitions_success(self):
        """Test successfully reading green steel definitions from Excel."""
        # Create a temporary Excel file with test data
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Create test data
            df = pd.DataFrame(
                {
                    "Grade": ["Level 1", "Level 2", "Level 3", "Level 4"],
                    "Threshold function (y <= b - m*x) parameter b": [0.4, 0.7, 1.4, 2.1],
                    "Threshold function (y <= b - m*x) parameter m": [0.005, 0.003, 0.002, 0.001],
                    "Threshold function (y <= b - m*x) - definition of x": [
                        "Scrap share (%)",
                        "Scrap share (%)",
                        "Scrap share (%)",
                        "Scrap share (%)",
                    ],
                }
            )

            # Write to Excel
            with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Green Steel Definitions", index=False)

            # Read the definitions
            grades = read_green_steel_definitions(tmp_path)

            # Verify the grades
            assert len(grades) == 4
            assert 1 in grades
            assert 2 in grades
            assert 3 in grades
            assert 4 in grades

            # Check Level 1
            grade1 = grades[1]
            assert grade1.level == 1
            assert grade1.name == "Level 1"
            assert grade1.b == 0.4
            assert grade1.m == 0.005

            # Check Level 4
            grade4 = grades[4]
            assert grade4.level == 4
            assert grade4.name == "Level 4"
            assert grade4.b == 2.1
            assert grade4.m == 0.001

        finally:
            # Clean up
            Path(tmp_path).unlink(missing_ok=True)

    def test_read_green_steel_definitions_missing_sheet(self):
        """Test handling of missing Green Steel Definitions sheet."""
        # Create Excel file without the required sheet
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            df = pd.DataFrame({"dummy": [1, 2, 3]})
            with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Other Sheet", index=False)

            # Should return empty dict for backward compatibility
            grades = read_green_steel_definitions(tmp_path)
            assert grades == {}

        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestFurnaceGroupGreenSteel:
    """Test green steel methods on FurnaceGroup."""

    def create_test_furnace_group(
        self,
        allocated_volumes: float = 1000,
        emissions: dict = None,
        metallic_charge: str = "iron_ore",
        scrap_ratio: float = 0.0,
    ) -> FurnaceGroup:
        """Helper to create a test furnace group."""
        # Create technology with feedstock
        technology = Technology(
            name="EAF",
            capex=500,
            product="steel",  # Add required product parameter
        )

        # Create primary feedstock
        feedstock = PrimaryFeedstock(metallic_charge=metallic_charge, reductant="electricity", technology="EAF")
        feedstock.required_quantity_per_ton_of_product = 1.0 - scrap_ratio

        # Add scrap feedstock if needed
        feedstocks = [feedstock]
        if scrap_ratio > 0:
            scrap_feedstock = PrimaryFeedstock(metallic_charge="scrap", reductant="electricity", technology="EAF")
            scrap_feedstock.required_quantity_per_ton_of_product = scrap_ratio
            feedstocks.append(scrap_feedstock)

        technology.dynamic_business_case = feedstocks

        # Default emissions if not provided
        if emissions is None:
            emissions = {"responsible_steel": {"scope1": 100, "scope2": 50, "scope3": 150}}

        furnace_group = FurnaceGroup(
            furnace_group_id="test_fg_001",
            capacity=Volumes(1000),
            status="operating",
            last_renovation_date=date(2020, 1, 1),
            technology=technology,
            historical_production={},
            utilization_rate=0.8,
            lifetime=PointInTime(
                current=Year(2024),
                time_frame=TimeFrame(start=Year(2020), end=Year(2040)),
                plant_lifetime=20,
            ),
        )

        # Set emissions and allocated volumes directly
        furnace_group.emissions = emissions
        furnace_group.allocated_volumes = allocated_volumes

        return furnace_group

    def test_calculate_emissions_intensity_with_production(self):
        """Test emissions intensity calculation with production."""
        fg = self.create_test_furnace_group(
            allocated_volumes=1000,
            emissions={
                "responsible_steel": {
                    "scope1": 500,  # 500 tCO2eq
                    "scope2": 300,  # 300 tCO2eq
                    "scope3": 200,  # 200 tCO2eq
                }
            },
        )

        # Total emissions = 1000 tCO2eq, production = 1000 t
        # Intensity = 1000/1000 = 1.0 tCO2eq/t
        assert fg.calculate_emissions_intensity() == 1.0

    def test_calculate_emissions_intensity_no_production(self):
        """Test emissions intensity returns 0 when no production."""
        fg = self.create_test_furnace_group(allocated_volumes=0)
        assert fg.calculate_emissions_intensity() == 0.0

    def test_calculate_scrap_share_all_scrap(self):
        """Test scrap share calculation with 100% scrap."""
        fg = self.create_test_furnace_group(metallic_charge="scrap", scrap_ratio=1.0)
        assert fg.calculate_scrap_share() == 100.0

    def test_calculate_scrap_share_no_scrap(self):
        """Test scrap share calculation with no scrap."""
        fg = self.create_test_furnace_group(metallic_charge="iron_ore", scrap_ratio=0.0)
        assert fg.calculate_scrap_share() == 0.0

    def test_calculate_scrap_share_mixed(self):
        """Test scrap share calculation with 30% scrap."""
        fg = self.create_test_furnace_group(metallic_charge="iron_ore", scrap_ratio=0.3)
        assert fg.calculate_scrap_share() == pytest.approx(30.0)

    def test_get_green_steel_grade_level1(self, tech_switches_csv, tmp_path):
        """Test getting green steel grade Level 1."""
        # Create environment with green steel grades
        config = create_test_config(tmp_path)
        env = Environment(config=config, tech_switches_csv=tech_switches_csv)
        env.green_steel_grades = {
            1: GreenSteelGrade(level=1, name="Level 1", b=0.4, m=0.5),
            2: GreenSteelGrade(level=2, name="Level 2", b=0.7, m=0.3),
            3: GreenSteelGrade(level=3, name="Level 3", b=1.4, m=0.2),
            4: GreenSteelGrade(level=4, name="Level 4", b=2.1, m=0.1),
        }

        # Create furnace group with low emissions and high scrap
        # 80% scrap, 0.05 tCO2eq/t emissions
        fg = self.create_test_furnace_group(
            allocated_volumes=1000,
            emissions={"responsible_steel": {"scope1": 50}},  # 50/1000 = 0.05 tCO2eq/t
            scrap_ratio=0.8,  # 80% scrap
        )

        # Level 1 threshold at 80% scrap: 0.4 - 0.5 * 0.8 = 0.0
        # Emissions of 0.05 > 0.0, so won't qualify for Level 1
        # Level 2 threshold at 80% scrap: 0.7 - 0.3 * 0.8 = 0.46 → qualifies ✓
        # Level 3 threshold at 80% scrap: 1.4 - 0.2 * 0.8 = 1.24 → qualifies ✓
        # Level 4 threshold at 80% scrap: 2.1 - 0.1 * 0.8 = 2.02 → qualifies ✓
        # Since we return max (highest/strictest grade), we expect 4
        assert fg.get_green_steel_grade(env) == 4

    def test_get_green_steel_grade_no_production(self, tech_switches_csv, tmp_path):
        """Test that furnace groups without production return None."""
        config = create_test_config(tmp_path)
        env = Environment(config=config, tech_switches_csv=tech_switches_csv)
        env.green_steel_grades = {
            1: GreenSteelGrade(level=1, name="Level 1", b=2.0, m=0.001),
        }

        fg = self.create_test_furnace_group(
            allocated_volumes=0,  # No production
            emissions={"responsible_steel": {"scope1": 0}},
        )

        # Should return None for no production
        assert fg.get_green_steel_grade(env) is None

    def test_get_green_steel_grade_no_definitions(self, tech_switches_csv, tmp_path):
        """Test handling when no green steel grades are defined."""
        config = create_test_config(tmp_path)
        env = Environment(config=config, tech_switches_csv=tech_switches_csv)
        env.green_steel_grades = {}  # No grades defined

        fg = self.create_test_furnace_group(allocated_volumes=1000)

        assert fg.get_green_steel_grade(env) is None

    def test_get_green_steel_grade_fails_all_levels(self, tech_switches_csv, tmp_path):
        """Test when furnace group doesn't qualify for any grade."""
        config = create_test_config(tmp_path)
        env = Environment(config=config, tech_switches_csv=tech_switches_csv)
        env.green_steel_grades = {
            1: GreenSteelGrade(level=1, name="Level 1", b=0.4, m=0.005),
            2: GreenSteelGrade(level=2, name="Level 2", b=0.7, m=0.003),
        }

        # Create furnace group with very high emissions
        fg = self.create_test_furnace_group(
            allocated_volumes=1000,
            emissions={"responsible_steel": {"scope1": 5000}},  # 5.0 tCO2eq/t
            scrap_ratio=0.0,  # No scrap
        )

        # Level 1 threshold at 0% scrap: 0.4 - 0.005 * 0 = 0.4
        # Level 2 threshold at 0% scrap: 0.7 - 0.003 * 0 = 0.7
        # Emissions of 5.0 > both thresholds
        assert fg.get_green_steel_grade(env) is None

    def test_get_green_steel_grade_multiple_qualify(self, tech_switches_csv, tmp_path):
        """Test that best (highest/strictest) grade is returned when multiple grades qualify."""
        config = create_test_config(tmp_path)
        env = Environment(config=config, tech_switches_csv=tech_switches_csv)
        env.green_steel_grades = {
            1: GreenSteelGrade(level=1, name="Level 1", b=0.4, m=0.002),
            2: GreenSteelGrade(level=2, name="Level 2", b=0.8, m=0.001),
            3: GreenSteelGrade(level=3, name="Level 3", b=1.5, m=0.001),
        }

        # Create furnace group that qualifies for all grades
        fg = self.create_test_furnace_group(
            allocated_volumes=1000,
            emissions={"responsible_steel": {"scope1": 200}},  # 0.2 tCO2eq/t
            scrap_ratio=0.5,  # 50% scrap
        )

        # With fraction conversion (50% = 0.5):
        # Level 1: 0.4 - 0.002 * 0.5 = 0.399, emissions 0.2 < 0.399 ✓
        # Level 2: 0.8 - 0.001 * 0.5 = 0.7995, emissions 0.2 < 0.7995 ✓
        # Level 3: 1.5 - 0.001 * 0.5 = 1.4995, emissions 0.2 < 1.4995 ✓
        # Should return best grade (highest number = strictest) = 3
        assert fg.get_green_steel_grade(env) == 3


class TestEnvironmentGreenSteel:
    """Test Environment green steel methods."""

    def test_initiate_green_steel_grades(self, tech_switches_csv, tmp_path):
        """Test initiating green steel grades in Environment."""
        config = create_test_config(tmp_path)
        env = Environment(config=config, tech_switches_csv=tech_switches_csv)

        grades_dict = {
            1: GreenSteelGrade(level=1, name="Level 1", b=0.4, m=0.005),
            2: GreenSteelGrade(level=2, name="Level 2", b=0.7, m=0.003),
        }

        env.initiate_green_steel_grades(grades_dict)

        assert len(env.green_steel_grades) == 2
        assert 1 in env.green_steel_grades
        assert 2 in env.green_steel_grades

        grade1 = env.green_steel_grades[1]
        assert isinstance(grade1, GreenSteelGrade)
        assert grade1.level == 1
        assert grade1.b == 0.4
        assert grade1.m == 0.005

    def test_initiate_green_steel_grades_empty(self, tech_switches_csv, tmp_path):
        """Test initiating with empty grades dict."""
        config = create_test_config(tmp_path)
        env = Environment(config=config, tech_switches_csv=tech_switches_csv)

        env.initiate_green_steel_grades({})
        assert env.green_steel_grades == {}
