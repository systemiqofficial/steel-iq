"""Integration test for the new SteelPlotter class.

Tests that all SteelPlotter methods generate plots correctly with consistent styling,
footers, and legends.
"""

import pytest
from pathlib import Path
from collections import defaultdict
import tempfile
import shutil

from steelo.utilities.steeliq_plotter import SteelPlotter, PlotConfig
from steelo.domain.models import PlotPaths


@pytest.fixture
def temp_plot_dir():
    """Create a temporary directory for plot outputs."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    # Cleanup after test
    shutil.rmtree(temp_dir)


@pytest.fixture
def plot_paths(temp_plot_dir):
    """Create PlotPaths for testing."""
    pam_plots_dir = temp_plot_dir / "PAM"
    pam_plots_dir.mkdir(parents=True, exist_ok=True)

    return PlotPaths(
        plots_dir=temp_plot_dir,
        pam_plots_dir=pam_plots_dir,
        geo_plots_dir=temp_plot_dir / "GEO",
        tm_plots_dir=temp_plot_dir / "TM",
    )


@pytest.fixture
def plotter(plot_paths):
    """Create SteelPlotter instance with default config."""
    config = PlotConfig()
    return SteelPlotter(config=config, plot_paths=plot_paths)


@pytest.fixture
def sample_capex_data():
    """Create sample CAPEX data for testing."""
    trace_capex = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    technologies = ["BF-BOF", "DRI-EAF", "Scrap-EAF", "H2-DRI"]
    years = range(2025, 2031)

    for year in years:
        for tech in technologies:
            # Simulate increasing CAPEX over time
            trace_capex[year][tech]["USA"] = (year - 2024) * 1e9 * (technologies.index(tech) + 1)
            trace_capex[year][tech]["CHN"] = (year - 2024) * 2e9 * (technologies.index(tech) + 1)

    return dict(trace_capex)


@pytest.fixture
def sample_emissions_data():
    """Create sample emissions data shaped as {boundary: {year: {tech: {scope: tCO2e}}}}.

    Includes a single boundary so tests assert against a deterministic file set, and a
    mix of magnitudes across direct, direct-with-biomass and indirect scopes so the
    plot exercises both the stacked-area renderer and the scope-split logic.
    """
    technologies = ["BF-BOF", "DRI-EAF", "Scrap-EAF", "H2-DRI"]
    years = range(2025, 2031)
    by_year: dict[int, dict[str, dict[str, float]]] = {}
    for year in years:
        per_tech: dict[str, dict[str, float]] = {}
        for tech in technologies:
            base = 100_000_000 * (5 - technologies.index(tech))
            scaled = base + (year - 2025) * 5_000_000
            per_tech[tech] = {
                "direct_ghg": scaled * 0.6,
                "direct_with_biomass_ghg": scaled * 0.6,
                "indirect_ghg": scaled * 0.4,
            }
        by_year[year] = per_tech
    return {"responsible_steel": by_year}


@pytest.fixture
def sample_production_by_product():
    """Iron and steel production totals by year (tonnes) for the emissions overlay."""
    return {
        year: {"iron": 1_500_000_000 + (year - 2025) * 10_000_000, "steel": 1_900_000_000 + (year - 2025) * 12_000_000}
        for year in range(2025, 2031)
    }


@pytest.fixture
def sample_iron_ore_data():
    """Create sample iron ore data for testing."""
    trace_iron_ore = defaultdict(lambda: defaultdict(float))
    qualities = ["io_high", "io_mid", "io_low"]
    years = range(2025, 2031)

    for year in years:
        for quality in qualities:
            # Simulate changing ore quality usage
            base_amount = 50000000
            if quality == "io_high":
                trace_iron_ore[year][quality] = base_amount + (year - 2025) * 2000000
            elif quality == "io_mid":
                trace_iron_ore[year][quality] = base_amount
            else:
                trace_iron_ore[year][quality] = base_amount - (year - 2025) * 1000000

    return dict(trace_iron_ore)


@pytest.fixture
def sample_metallic_charges_data():
    """Create sample metallic charges data for testing."""
    trace_metallic_charges = defaultdict(lambda: defaultdict(float))
    charges = ["hot metal", "scrap", "dri", "pig iron"]
    years = range(2025, 2031)

    for year in years:
        for charge in charges:
            # Simulate changing metallic charge usage
            trace_metallic_charges[year][charge] = 80000000 * (charges.index(charge) + 1) + (year - 2025) * 3000000

    return dict(trace_metallic_charges)


class TestSteelPlotter:
    """Test suite for SteelPlotter class."""

    def test_plot_capex_by_technology_creates_file(self, plotter, plot_paths, sample_capex_data):
        """Test that CAPEX plotting creates a PNG file."""
        plotter.plot_capex_by_technology(trace_capex=sample_capex_data)

        plot_file = plot_paths.pam_plots_dir / "capex_by_technology_and_year.png"
        assert plot_file.exists(), "CAPEX plot file was not created"
        assert plot_file.stat().st_size > 0, "CAPEX plot file is empty"

    def test_plot_emissions_by_technology_creates_file(
        self,
        plotter,
        plot_paths,
        sample_emissions_data,
        sample_production_by_product,
    ):
        """Test that emissions plotting writes the five scope variants per boundary into the emissions subfolder."""
        from steelo.utilities.steeliq_plotter import SteelPlotter

        plotter.plot_emissions_by_technology(
            trace_emissions=sample_emissions_data,
            trace_production_by_product=sample_production_by_product,
        )

        emissions_dir = plot_paths.plots_dir / SteelPlotter.EMISSIONS_SUBDIR
        boundary = next(iter(sample_emissions_data))
        expected = [
            f"emissions_direct_by_technology__{boundary}.png",
            f"emissions_direct_with_biomass_by_technology__{boundary}.png",
            f"emissions_indirect_by_technology__{boundary}.png",
            f"emissions_direct_plus_indirect_by_technology__{boundary}.png",
            f"emissions_direct_with_biomass_plus_indirect_by_technology__{boundary}.png",
        ]
        for filename in expected:
            plot_file = emissions_dir / filename
            assert plot_file.exists(), f"{filename} was not created"
            assert plot_file.stat().st_size > 0, f"{filename} is empty"

    def test_plot_iron_ore_by_quality_creates_file(self, plotter, plot_paths, sample_iron_ore_data):
        """Test that iron ore plotting creates a PNG file."""
        plotter.plot_iron_ore_by_quality(trace_iron_ore=sample_iron_ore_data)

        plot_file = plot_paths.pam_plots_dir / "iron_ore_by_quality_over_time.png"
        assert plot_file.exists(), "Iron ore plot file was not created"
        assert plot_file.stat().st_size > 0, "Iron ore plot file is empty"

    def test_plot_metallic_charges_creates_file(self, plotter, plot_paths, sample_metallic_charges_data):
        """Test that metallic charges plotting creates a PNG file."""
        plotter.plot_metallic_charges(trace_metallic_charges=sample_metallic_charges_data)

        plot_file = plot_paths.pam_plots_dir / "metallic_charges_over_time.png"
        assert plot_file.exists(), "Metallic charges plot file was not created"
        assert plot_file.stat().st_size > 0, "Metallic charges plot file is empty"

    def test_all_plots_generated(
        self,
        plotter,
        plot_paths,
        sample_capex_data,
        sample_emissions_data,
        sample_iron_ore_data,
        sample_metallic_charges_data,
        sample_production_by_product,
    ):
        """Test that all plotting methods work together; emissions go to their subfolder."""
        from steelo.utilities.steeliq_plotter import SteelPlotter

        plotter.plot_capex_by_technology(trace_capex=sample_capex_data)
        plotter.plot_emissions_by_technology(
            trace_emissions=sample_emissions_data,
            trace_production_by_product=sample_production_by_product,
        )
        plotter.plot_iron_ore_by_quality(trace_iron_ore=sample_iron_ore_data)
        plotter.plot_metallic_charges(trace_metallic_charges=sample_metallic_charges_data)

        # Three top-level PNGs (capex, iron ore, metallic charges) live directly in pam_plots_dir
        top_level = list(plot_paths.pam_plots_dir.glob("*.png"))
        assert len(top_level) == 3, f"Expected 3 top-level PNGs, found {len(top_level)}"

        # Five emissions PNGs (one per scope view) live under the emissions subfolder
        emissions_dir = plot_paths.plots_dir / SteelPlotter.EMISSIONS_SUBDIR
        emissions_files = list(emissions_dir.glob("*.png"))
        assert len(emissions_files) == 5, f"Expected 5 emissions PNGs, found {len(emissions_files)}"

        for plot_file in top_level + emissions_files:
            size_kb = plot_file.stat().st_size / 1024
            assert size_kb > 50, f"{plot_file.name} is too small ({size_kb:.1f} KB)"

    def test_custom_config(self, plot_paths, sample_capex_data):
        """Test that custom PlotConfig is respected."""
        # Create custom config
        custom_config = PlotConfig(
            footer_template="Custom footer: {date}",
            show_footer=True,
            default_dpi=150,
        )

        plotter = SteelPlotter(config=custom_config, plot_paths=plot_paths)
        plotter.plot_capex_by_technology(trace_capex=sample_capex_data)

        plot_file = plot_paths.pam_plots_dir / "capex_by_technology_and_year.png"
        assert plot_file.exists(), "Plot with custom config was not created"

    def test_empty_data_handling(self, plotter):
        """Test that plotter handles empty data gracefully."""
        # Test with empty data - should not crash
        plotter.plot_capex_by_technology(trace_capex={})
        plotter.plot_emissions_by_technology(trace_emissions={})
        plotter.plot_iron_ore_by_quality(trace_iron_ore={})
        plotter.plot_metallic_charges(trace_metallic_charges={})

    def test_plot_capacity_development_by_technology(self, plotter, plot_paths):
        """Test that capacity development plotting creates a PNG file."""
        # Create sample DataFrame that mimics post-processed output
        import pandas as pd

        data = []
        technologies = ["BF-BOF", "DRI-EAF", "Scrap-EAF"]
        years = [2025, 2026, 2027, 2028]

        for year in years:
            for tech_idx, tech in enumerate(technologies):
                # Create multiple furnace groups per technology
                for fg_num in range(3):
                    data.append(
                        {
                            "furnace_group_id": f"FG_{tech}_{year}_{fg_num}",
                            "year": year,
                            "technology": tech,
                            "capacity": 1000000 * (tech_idx + 1) + year * 50000,  # Growing capacity
                            "product": "steel",
                        }
                    )

        df = pd.DataFrame(data)

        plotter.plot_capacity_development_by_technology(data_file=df, units="Mt")

        plot_file = plot_paths.pam_plots_dir / "Capacity_development_by_technology.png"
        assert plot_file.exists(), "Capacity development plot file was not created"
        assert plot_file.stat().st_size > 0, "Capacity development plot file is empty"

    def test_plot_area_chart_by_region(self, plotter, plot_paths):
        """Test area chart plotting by region."""
        import pandas as pd

        data = []
        regions = ["North America", "Europe", "Asia"]
        years = [2025, 2026, 2027, 2028]

        for year in years:
            for region_idx, region in enumerate(regions):
                for fg_num in range(2):
                    data.append(
                        {
                            "furnace_group_id": f"FG_{region}_{year}_{fg_num}",
                            "year": year,
                            "region": region,
                            "technology": "BF-BOF",
                            "capacity": 1000000 * (region_idx + 1) + year * 10000,
                            "production": 900000 * (region_idx + 1) + year * 9000,
                            "product": "steel",
                        }
                    )

        df = pd.DataFrame(data)

        plotter.plot_area_chart_by_region_or_technology(
            dataframe=df,
            column_name="capacity",
            title="Steel Capacity by Region",
            units="Mtpa",
            pivot_columns=["region"],
            product_type="steel",
        )

        plot_file = plot_paths.pam_plots_dir / "steel_capacity_development_by_region.png"
        assert plot_file.exists(), "Area chart by region was not created"
        assert plot_file.stat().st_size > 0, "Area chart by region is empty"

    def test_plot_area_chart_by_technology(self, plotter, plot_paths):
        """Test area chart plotting by technology."""
        import pandas as pd

        data = []
        technologies = ["BF-BOF", "DRI-EAF", "Scrap-EAF"]
        years = [2025, 2026, 2027, 2028]

        for year in years:
            for tech_idx, tech in enumerate(technologies):
                for fg_num in range(2):
                    data.append(
                        {
                            "furnace_group_id": f"FG_{tech}_{year}_{fg_num}",
                            "year": year,
                            "region": "Europe",
                            "technology": tech,
                            "capacity": 1000000 * (tech_idx + 1) + year * 10000,
                            "production": 900000 * (tech_idx + 1) + year * 9000,
                            "product": "steel",
                        }
                    )

        df = pd.DataFrame(data)

        plotter.plot_area_chart_by_region_or_technology(
            dataframe=df,
            column_name="production",
            title="Steel Production by Technology",
            units="Mtpa",
            pivot_columns=["technology"],
            product_type="steel",
        )

        plot_file = plot_paths.pam_plots_dir / "steel_production_development_by_technology.png"
        assert plot_file.exists(), "Area chart by technology was not created"
        assert plot_file.stat().st_size > 0, "Area chart by technology is empty"
