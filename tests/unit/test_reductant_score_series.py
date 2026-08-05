"""Tests for the year-wise reductant-optimised score series (B3 core)."""

from pathlib import Path

import pytest

from steelo.domain.calculate_costs import build_direct_ghg_lookup, score_reductants_for_business_cases
from steelo.domain.models import (
    CountryMapping,
    Environment,
    Location,
    PrimaryFeedstock,
    Subsidy,
    TechnologyEmissionFactors,
    Year,
)
from steelo.simulation import SimulationConfig

YEARS = [Year(y) for y in range(2025, 2031)]
LOCATION = Location(lat=0.0, lon=0.0, country="Aland", region="TestRegion", iso3="AAA")


def _feedstock(reductant: str, energy: dict[str, float]) -> PrimaryFeedstock:
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant=reductant, technology="DRI")
    pf.required_quantity_per_ton_of_product = 1.5
    for carrier, volume in energy.items():
        pf.add_energy_requirement(carrier, volume)
    return pf


def _ef(reductant: str, factor: float) -> TechnologyEmissionFactors:
    return TechnologyEmissionFactors(
        business_case=f"IO_low_{reductant}_DRI",
        technology="DRI",
        boundary="rs-inspired",
        metallic_charge="IO_low",
        reductant=reductant,
        direct_ghg_factor=factor,
        direct_with_biomass_ghg_factor=factor,
        indirect_ghg_factor=0.0,
    )


def _make_env(tmp_path: Path, carbon_by_year: dict[int, float] | None = None) -> Environment:
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,DRI\nDRI,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    env.year = Year(2025)
    # Electricity falls steeply (drives LCOH down); natural gas rises slowly.
    env.input_costs = {
        "AAA": {
            year: {"electricity": 0.10 - 0.015 * i, "natural_gas": 0.03 + 0.002 * i} for i, year in enumerate(YEARS)
        }
    }
    env.hydrogen_efficiency = {year: 0.05 for year in YEARS}
    env.hydrogen_capex_opex = {"AAA": {year: 1.0 for year in YEARS}}
    env.initiate_country_mappings(
        country_mappings=[
            CountryMapping(
                country="Aland",
                iso2="AA",
                iso3="AAA",
                irena_name="Aland",
                region_for_outputs="TestRegion",
                ssp_region="TestRegion",
                tiam_ucl_region="TestRegion",
            )
        ]
    )
    env.initiate_capped_hydrogen_costs_by_year()
    # DRI with a cheap-now fossil reductant and a clean one that wins once LCOH falls.
    env.initiate_dynamic_feedstocks(
        [
            _feedstock("natural_gas", {"natural_gas": 10.0}),
            _feedstock("hydrogen", {"hydrogen": 0.055}),
        ]
    )
    env.initiate_technology_emission_factors([_ef("natural_gas", 1.4), _ef("hydrogen", 0.1)])
    if carbon_by_year is not None:
        env.carbon_costs = {"AAA": {Year(y): price for y, price in carbon_by_year.items()}}
    return env


SHARES = {"IO_low": 1.0}


def _fixed_reductant_scores(env: Environment, reductant: str, years: list[Year]) -> list[float]:
    """Brute-force per-year score for one fixed reductant, via the shared scorer."""
    ef_by_key = build_direct_ghg_lookup(env.technology_emission_factors, "rs-inspired")
    values = []
    for year in years:
        input_costs, output_costs = env.candidate_energy_costs_for_year(LOCATION, "DRI", year)
        scores = score_reductants_for_business_cases(
            env.dynamic_feedstocks["dri"],
            input_costs,
            output_costs,
            env.carbon_price_for_year("AAA", year),
            ef_by_key,
            env.config.disposal_cost_outputs,
        )
        values.append(scores.score_by_input["IO_low"][reductant])
    return values


def test_series_equals_pointwise_min_of_fixed_reductant_scores(tmp_path: Path) -> None:
    """The year-wise series is the pointwise minimum over fixed-reductant score series."""
    env = _make_env(tmp_path, carbon_by_year={y: 100.0 for y in range(2025, 2031)})
    series = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))

    gas = _fixed_reductant_scores(env, "natural_gas", YEARS)
    hydrogen = _fixed_reductant_scores(env, "hydrogen", YEARS)
    assert series.scores == pytest.approx([min(g, h) for g, h in zip(gas, hydrogen)])


def test_series_picks_flip_at_the_crossover(tmp_path: Path) -> None:
    """Falling LCOH flips the pick from natural gas to hydrogen mid-horizon."""
    env = _make_env(tmp_path, carbon_by_year={y: 100.0 for y in range(2025, 2031)})
    series = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))

    assert series.picks[0] == "natural_gas"
    assert series.picks[-1] == "hydrogen"
    flip = series.picks.index("hydrogen")
    assert all(pick == "hydrogen" for pick in series.picks[flip:])


def test_series_pick_matches_annual_repick_rule(tmp_path: Path) -> None:
    """For any year, the series pick equals what the B5 annual re-pick would choose."""
    from steelo.domain.models import FurnaceGroup, PointInTime, Technology, TimeFrame

    env = _make_env(tmp_path, carbon_by_year={y: 100.0 for y in range(2025, 2031)})
    series = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))

    for i, year in enumerate(YEARS):
        input_costs, output_costs = env.candidate_energy_costs_for_year(LOCATION, "DRI", year)
        fg = FurnaceGroup(
            furnace_group_id="fg_test",
            capacity=1000.0,
            status="operating",
            last_renovation_date=None,
            technology=Technology(name="DRI", product="dri", dynamic_business_case=env.dynamic_feedstocks["dri"]),
            historical_production={},
            utilization_rate=0.8,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2025), end=Year(2045)),
                plant_lifetime=20,
            ),
            chosen_reductant="",
        )
        fg.energy_costs = input_costs
        fg.output_energy_costs = output_costs
        fg.disposal_cost_outputs = env.config.disposal_cost_outputs
        fg.generate_energy_vopex_by_reductant(
            carbon_price=env.carbon_price_for_year("AAA", year),
            technology_emission_factors=env.technology_emission_factors,
            chosen_emissions_boundary="rs-inspired",
        )
        assert fg.chosen_reductant == series.picks[i], f"year {year}"


def test_candidate_subsidy_expiry_moves_the_pick(tmp_path: Path) -> None:
    """A hydrogen subsidy scoped to the candidate flips the pick only while active."""
    env = _make_env(tmp_path, carbon_by_year={y: 100.0 for y in range(2025, 2031)})
    baseline = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))
    first_hydrogen_year = baseline.picks.index("hydrogen")
    assert first_hydrogen_year > 1  # gas wins early without support

    env.initiate_energy_subsidies(
        [
            Subsidy(
                scenario_name="test",
                iso3="AAA",
                start_year=Year(2025),
                end_year=Year(2025),
                technology_name="DRI",
                cost_item="hydrogen",
                subsidy_type="relative",
                subsidy_amount=0.9,
            )
        ]
    )
    subsidised = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))

    assert subsidised.picks[0] == "hydrogen"  # subsidy active
    assert subsidised.picks[1] == "natural_gas"  # expired, back to gas
    assert subsidised.picks[first_hydrogen_year:] == baseline.picks[first_hydrogen_year:]


def test_scores_clamp_beyond_the_data_horizon(tmp_path: Path) -> None:
    """Years past the input-cost horizon reuse the last data year's prices."""
    env = _make_env(tmp_path, carbon_by_year={y: 100.0 for y in range(2025, 2051)})
    series = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2029), Year(2036))

    assert series.scores[1] == pytest.approx(series.scores[-1])  # 2030 == 2035
    assert series.picks[1] == series.picks[-1]


def test_site_overrides_follow_the_country_trajectory(tmp_path: Path) -> None:
    """A site electricity override is scaled by the country trajectory ratio."""
    env = _make_env(tmp_path, carbon_by_year={y: 0.0 for y in range(2025, 2031)})
    # Site pays half the country electricity price in 2025.
    site_electricity = 0.05
    input_2027, _ = env.candidate_energy_costs_for_year(
        LOCATION,
        "DRI",
        Year(2027),
        overrides={"electricity": site_electricity},
        override_reference_year=Year(2025),
    )
    country_2025 = env.input_costs["AAA"][Year(2025)]["electricity"]
    country_2027 = env.input_costs["AAA"][Year(2027)]["electricity"]

    assert input_2027["electricity"] == pytest.approx(site_electricity * country_2027 / country_2025)


def test_override_reference_ignores_reference_year_subsidies(tmp_path: Path) -> None:
    """A subsidy active in the reference year must not leak into the override scaling ratio.

    The trajectory ratio country_t / country_ref is defined on unsubsidised prices;
    a candidate electricity subsidy live in the reference year previously deflated
    the denominator and inflated every scaled year.
    """
    env = _make_env(tmp_path, carbon_by_year={y: 0.0 for y in range(2025, 2031)})
    env.initiate_energy_subsidies(
        [
            Subsidy(
                scenario_name="test",
                iso3="AAA",
                start_year=Year(2025),
                end_year=Year(2025),
                technology_name="DRI",
                cost_item="electricity",
                subsidy_type="relative",
                subsidy_amount=0.5,
            )
        ]
    )
    site_electricity = 0.05
    input_2027, _ = env.candidate_energy_costs_for_year(
        LOCATION,
        "DRI",
        Year(2027),
        overrides={"electricity": site_electricity},
        override_reference_year=Year(2025),
    )
    country_2025 = env.input_costs["AAA"][Year(2025)]["electricity"]
    country_2027 = env.input_costs["AAA"][Year(2027)]["electricity"]

    # Subsidy expired by 2027: the scaled price is exactly the unsubsidised ratio.
    assert input_2027["electricity"] == pytest.approx(site_electricity * country_2027 / country_2025)
