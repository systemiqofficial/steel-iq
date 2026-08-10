"""Tests for the bootstrap precompute of the capped-LCOH series."""

from pathlib import Path

import pytest

from steelo.domain.models import CountryMapping, Environment, Year
from steelo.simulation import SimulationConfig


def _make_mapping(country: str, iso2: str, iso3: str, region: str) -> CountryMapping:
    return CountryMapping(
        country=country,
        iso2=iso2,
        iso3=iso3,
        irena_name=country,
        region_for_outputs=region,
        ssp_region=region,
        tiam_ucl_region=region,
    )


def _make_env(tmp_path: Path) -> Environment:
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2027),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF\nBF,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    env.year = Year(2025)
    years = [Year(2025), Year(2026), Year(2027)]
    env.input_costs = {
        "AAA": {y: {"electricity": 0.05 + 0.01 * i} for i, y in enumerate(years)},
        "BBB": {y: {"electricity": 0.09 - 0.01 * i} for i, y in enumerate(years)},
    }
    env.hydrogen_efficiency = {y: 0.05 for y in years}
    env.hydrogen_capex_opex = {
        "AAA": {y: 1.0 for y in years},
        "BBB": {y: 1.5 for y in years},
    }
    env.initiate_country_mappings(
        country_mappings=[
            _make_mapping("Aland", "AA", "AAA", "TestRegion"),
            _make_mapping("Bland", "BB", "BBB", "TestRegion"),
        ]
    )
    return env


def test_precomputed_series_matches_per_year_computation(tmp_path: Path) -> None:
    """Each precomputed year equals a direct per-year computation, including the env.year path."""
    env = _make_env(tmp_path)
    env.initiate_capped_hydrogen_costs_by_year()

    assert sorted(env.capped_hydrogen_costs_by_year) == [Year(2025), Year(2026), Year(2027)]
    for year in env.capped_hydrogen_costs_by_year:
        assert env.capped_hydrogen_costs_by_year[year] == env.calculate_capped_hydrogen_costs_per_country(year=year)
        env.year = year
        assert env.capped_hydrogen_costs_for_year(year) == env.calculate_capped_hydrogen_costs_per_country()


def test_prices_vary_across_years(tmp_path: Path) -> None:
    """The series reflects the year-indexed electricity trajectory, not a frozen snapshot."""
    env = _make_env(tmp_path)
    env.initiate_capped_hydrogen_costs_by_year()

    aaa_prices = [env.capped_hydrogen_costs_for_year(Year(y))["AAA"] for y in (2025, 2026, 2027)]
    assert aaa_prices[0] < aaa_prices[1] < aaa_prices[2]


def test_lookup_beyond_horizon_clamps_to_last_year(tmp_path: Path) -> None:
    """Years past the data end reuse the last covered year's prices."""
    env = _make_env(tmp_path)
    env.initiate_capped_hydrogen_costs_by_year()

    assert env.capped_hydrogen_costs_for_year(Year(2040)) == env.capped_hydrogen_costs_for_year(Year(2027))


def test_lookup_before_precompute_raises(tmp_path: Path) -> None:
    """The accessor fails loudly when the series was never precomputed."""
    env = _make_env(tmp_path)

    with pytest.raises(ValueError, match="not precomputed"):
        env.capped_hydrogen_costs_for_year(Year(2025))


def test_lookup_before_covered_range_raises(tmp_path: Path) -> None:
    """Years before the covered range raise rather than silently degrading."""
    env = _make_env(tmp_path)
    env.initiate_capped_hydrogen_costs_by_year()

    with pytest.raises(KeyError):
        env.capped_hydrogen_costs_for_year(Year(2020))
