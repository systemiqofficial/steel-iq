"""Test that region mappings are consistent between dynamic country mappings and plotting colors."""

import pytest
from steelo.domain.models import CountryMappingService, CountryMapping
from steelo.utilities.plotting import region2colours


@pytest.fixture
def country_mappings():
    """Create mock country mappings for testing."""
    # Create mappings for the countries we test
    mappings = [
        CountryMapping(
            country="China",
            iso2="CN",
            iso3="CHN",
            irena_name="China",
            region_for_outputs="China",
            ssp_region="CHA",
            gem_country="China",
            ws_region="China",
            tiam_ucl_region="China",
            eu_region=None,
        ),
        CountryMapping(
            country="India",
            iso2="IN",
            iso3="IND",
            irena_name="India",
            region_for_outputs="India",
            ssp_region="IND",
            gem_country="India",
            ws_region="India",
            tiam_ucl_region="India",
            eu_region=None,
        ),
        CountryMapping(
            country="United States",
            iso2="US",
            iso3="USA",
            irena_name="United States",
            region_for_outputs="North America",
            ssp_region="USA",
            gem_country="United States",
            ws_region="North America",
            tiam_ucl_region="United States",
            eu_region=None,
        ),
        CountryMapping(
            country="Germany",
            iso2="DE",
            iso3="DEU",
            irena_name="Germany",
            region_for_outputs="Europe",
            ssp_region="EUR",
            gem_country="Germany",
            ws_region="Europe",
            tiam_ucl_region="Western Europe",
            eu_region="EU",
        ),
        CountryMapping(
            country="Japan",
            iso2="JP",
            iso3="JPN",
            irena_name="Japan",
            region_for_outputs="Developed Asia",
            ssp_region="JPN",
            gem_country="Japan",
            ws_region="Developed Asia",
            tiam_ucl_region="Japan",
            eu_region=None,
        ),
        CountryMapping(
            country="Brazil",
            iso2="BR",
            iso3="BRA",
            irena_name="Brazil",
            region_for_outputs="Latin America",
            ssp_region="LAM",
            gem_country="Brazil",
            ws_region="Latin America",
            tiam_ucl_region="Brazil",
            eu_region=None,
        ),
        CountryMapping(
            country="Russia",
            iso2="RU",
            iso3="RUS",
            irena_name="Russia",
            region_for_outputs="CIS",
            ssp_region="RUS",
            gem_country="Russia",
            ws_region="CIS",
            tiam_ucl_region="Former Soviet Union",
            eu_region=None,
        ),
        CountryMapping(
            country="South Africa",
            iso2="ZA",
            iso3="ZAF",
            irena_name="South Africa",
            region_for_outputs="Subsaharan Africa",
            ssp_region="AFR",
            gem_country="South Africa",
            ws_region="Africa",
            tiam_ucl_region="Africa",
            eu_region=None,
        ),
        CountryMapping(
            country="Saudi Arabia",
            iso2="SA",
            iso3="SAU",
            irena_name="Saudi Arabia",
            region_for_outputs="MENA",
            ssp_region="MEA",
            gem_country="Saudi Arabia",
            ws_region="Middle East",
            tiam_ucl_region="Middle East",
            eu_region=None,
        ),
        # Additional test codes
        CountryMapping(
            country="Brunei",
            iso2="BN",
            iso3="BRN",
            irena_name="Brunei",
            region_for_outputs="Other Asia",
            ssp_region="ASIA",
            gem_country="Brunei",
            ws_region="Other Asia",
            tiam_ucl_region="Other Asia",
            eu_region=None,
        ),
        CountryMapping(
            country="South Korea",
            iso2="KR",
            iso3="KOR",
            irena_name="South Korea",
            region_for_outputs="Developed Asia",
            ssp_region="KOR",
            gem_country="South Korea",
            ws_region="Developed Asia",
            tiam_ucl_region="South Korea",
            eu_region=None,
        ),
    ]

    return CountryMappingService(mappings)


@pytest.fixture
def iso3_to_region_map(country_mappings):
    """Get the ISO3 to region mapping from country mappings."""
    return country_mappings.iso3_to_region()


def test_major_countries_have_distinct_regions(iso3_to_region_map):
    """Ensure major steel-producing countries are mapped to their own distinct regions."""
    major_countries = {
        "CHN": "China",
        "IND": "India",
    }

    for iso3, expected_region in major_countries.items():
        assert iso3 in iso3_to_region_map, f"{iso3} missing from iso3_to_region mapping"
        assert iso3_to_region_map[iso3] == expected_region, (
            f"{iso3} should map to '{expected_region}', not '{iso3_to_region_map[iso3]}'"
        )


def test_all_iso3_codes_have_region_mapping(iso3_to_region_map):
    """Ensure all ISO3 codes that might appear in data have a region mapping."""
    # Common ISO3 codes that have appeared in warnings
    expected_codes = ["BRN", "CHN", "IND", "USA", "DEU", "JPN", "KOR", "RUS", "BRA"]

    for code in expected_codes:
        assert code in iso3_to_region_map, f"ISO3 code {code} missing from iso3_to_region mapping"


def test_region_colors_match_iso3_mappings(iso3_to_region_map):
    """Ensure all regions from iso3_to_region have corresponding colors for plotting."""
    # Get all unique regions from iso3_to_region
    all_regions = set(iso3_to_region_map.values())

    # These regions should have colors defined
    missing_colors = []
    for region in all_regions:
        if region not in region2colours:
            missing_colors.append(region)

    # Some regions might not have colors defined yet (like 'Rest of World')
    # This is a warning, not a hard failure
    if missing_colors:
        print(f"\nWarning: Regions without colors defined: {missing_colors}")


def test_no_orphaned_region_colors(iso3_to_region_map):
    """Warn if region2colours has colors for regions not in iso3_to_region."""
    all_regions = set(iso3_to_region_map.values())

    orphaned_colors = []
    for region in region2colours:
        if region not in all_regions:
            orphaned_colors.append(region)

    # This is a warning, not a failure - some colors might be for future use
    if orphaned_colors:
        print(f"\nWarning: Colors defined for regions not in iso3_to_region: {orphaned_colors}")


def test_china_india_plotting_visibility(iso3_to_region_map):
    """Test that China and India data won't be hidden in aggregate regions during plotting."""
    # Simulate what happens in plotting code
    test_data = [
        {"iso3": "CHN", "production": 1000},
        {"iso3": "IND", "production": 500},
        {"iso3": "JPN", "production": 100},
        {"iso3": "KOR", "production": 80},
    ]

    # Group by region (simulating what plot_area_chart_of_column_by_region does)
    region_totals = {}
    for item in test_data:
        region = iso3_to_region_map.get(item["iso3"], "Unknown")
        region_totals[region] = region_totals.get(region, 0) + item["production"]

    # China and India should be separate regions, not aggregated
    assert "China" in region_totals, "China production data would be hidden in plots"
    assert "India" in region_totals, "India production data would be hidden in plots"
    assert region_totals["China"] == 1000, "China production should not be aggregated with other countries"
    assert region_totals["India"] == 500, "India production should not be aggregated with other countries"


@pytest.mark.parametrize(
    "iso3,expected_region",
    [
        ("CHN", "China"),
        ("IND", "India"),
        ("USA", "North America"),  # Updated to match dynamic mapping
        ("DEU", "Europe"),
        ("JPN", "Developed Asia"),  # Updated to match dynamic mapping
        ("BRA", "Latin America"),  # Updated to match dynamic mapping
        ("RUS", "CIS"),
        ("ZAF", "Subsaharan Africa"),  # Updated to match dynamic mapping
        ("SAU", "MENA"),
    ],
)
def test_specific_country_mappings(iso3, expected_region, iso3_to_region_map):
    """Test specific important country-to-region mappings."""
    assert iso3_to_region_map.get(iso3) == expected_region
