"""Tests for the year-wise reductant-optimised score series (B3 core)."""

from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from steelo.domain.calculate_costs import build_direct_ghg_lookup, score_reductants_for_business_cases
from steelo.domain.models import (
    CaptiveEnergy,
    CountryMapping,
    Environment,
    Location,
    PrimaryFeedstock,
    Subsidy,
    TechnologyEmissionFactors,
    Year,
)
from steelo.simulation import GeoConfig, SimulationConfig

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
        geo_config=GeoConfig(included_power_mix="Grid only"),  # hydrogen off the grid price; no baseload LCOE here
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


def test_captive_prices_take_only_their_own_years_energy_subsidies(tmp_path: Path) -> None:
    """A subsidy live in the lock year reaches neither the stack nor a later year's price; it applies in its own year.

    The stack is built from unsubsidised inputs, and each year's subsidies apply on top of that year's captive price,
    so a subsidy active only in 2025 halves the 2025 electricity price and leaves 2027's at the stack's own.
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
    captive = CaptiveEnergy(Year(2025), 0.85, 0.04, 0.05, 1.0, None)

    input_2025, _ = env.candidate_energy_costs_for_year(LOCATION, "DRI", Year(2025), captive=captive)
    input_2027, _ = env.candidate_energy_costs_for_year(LOCATION, "DRI", Year(2027), captive=captive)

    grid_2025 = env.input_costs["AAA"][Year(2025)]["electricity"]
    grid_2027 = env.input_costs["AAA"][Year(2027)]["electricity"]
    assert input_2025["electricity"] == pytest.approx(0.5 * captive.electricity(grid_2025))
    assert input_2027["electricity"] == pytest.approx(captive.electricity(grid_2027))


def _reference_series(env, location, technology_name, shares, start, end, captive=None):
    """The series as computed without the memo: score each year from scratch."""
    business_case = env.dynamic_feedstocks[technology_name.lower()]
    ef_by_key = build_direct_ghg_lookup(
        env.technology_emission_factors, env.config.chosen_emissions_boundary_for_carbon_costs
    )
    scores, picks = [], []
    for year in range(int(start), int(end)):
        input_costs, output_costs = env.candidate_energy_costs_for_year(
            location, technology_name, Year(year), captive=captive
        )
        year_scores = score_reductants_for_business_cases(
            business_case,
            input_costs,
            output_costs,
            env.carbon_price_for_year(location.iso3, Year(year)),
            ef_by_key,
            env.config.disposal_cost_outputs,
        )
        mins = [min(costs, key=lambda k: costs[k]) for costs in year_scores.score_by_input.values() if costs]
        pick, _ = Counter(mins).most_common(1)[0]
        scores.append(
            sum(
                share * year_scores.score_by_input[charge][pick]
                for charge, share in shares.items()
                if pick in year_scores.score_by_input.get(charge, {})
            )
        )
        picks.append(pick)
    return scores, picks


def test_memoised_series_equals_fresh_computation_across_windows_shares_and_stacks(tmp_path: Path) -> None:
    env = _make_env(tmp_path, carbon_by_year={y: 100.0 for y in range(2025, 2031)})
    windows = [(2025, 2031), (2026, 2031), (2025, 2028), (2027, 2034), (2025, 2031)]  # overlapping and repeated
    cheap = CaptiveEnergy(Year(2026), 0.85, 0.02, 0.05, 1.0, None)
    cases = [
        ({"IO_low": 1.0}, None),
        ({"IO_low": 0.4}, None),  # shares only weight the picked scores
        ({"IO_low": 1.0}, cheap),
        ({"IO_low": 0.4}, cheap),
        ({"IO_low": 1.0}, CaptiveEnergy(Year(2026), 0.85, 0.09, 0.05, 1.0, None)),  # another stack
        ({"IO_low": 1.0}, CaptiveEnergy(Year(2027), 0.85, 0.02, 0.05, 1.0, None)),  # same fields, another lock year
        ({"IO_low": 1.0}, CaptiveEnergy(Year(2026), 0.85, 0.02, 0.05, 1.0, 9.0)),  # same, on contract
    ]
    for start, end in windows:
        for shares, captive in cases:
            series = env.reductant_score_series(LOCATION, "DRI", shares, Year(start), Year(end), captive=captive)
            scores, picks = _reference_series(env, LOCATION, "DRI", shares, start, end, captive=captive)
            assert series.scores == scores
            assert series.picks == picks


def test_memo_is_dropped_when_exogenous_inputs_are_replaced_or_reinitialised(tmp_path: Path) -> None:
    env = _make_env(tmp_path, carbon_by_year={y: 0.0 for y in range(2025, 2031)})
    before = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))

    env.carbon_costs = {"AAA": {Year(y): 500.0 for y in range(2025, 2031)}}  # replaced container
    replaced = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))
    assert replaced.scores == _reference_series(env, LOCATION, "DRI", SHARES, 2025, 2031)[0]
    assert replaced.scores != before.scores

    env.initiate_technology_emission_factors([_ef("natural_gas", 0.0), _ef("hydrogen", 0.1)])  # re-initialised
    reinitialised = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))
    assert reinitialised.scores == _reference_series(env, LOCATION, "DRI", SHARES, 2025, 2031)[0]
    assert reinitialised.scores != replaced.scores


def test_memo_follows_in_place_changes_only_after_it_is_cleared(tmp_path: Path) -> None:
    env = _make_env(tmp_path, carbon_by_year={y: 0.0 for y in range(2025, 2031)})
    before = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))

    env.carbon_costs["AAA"] = {Year(y): 500.0 for y in range(2025, 2031)}  # in place: not detected
    assert env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031)).scores == before.scores

    env.clear_reductant_score_cache()
    after = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))
    assert after.scores == _reference_series(env, LOCATION, "DRI", SHARES, 2025, 2031)[0]
    assert after.scores != before.scores


def _make_captive_env(tmp_path: Path, grid_by_year: dict[int, float], site_ceiling: float) -> Environment:
    """
    An environment pricing one new site at (0, 0) in AAA as a captive system, 2025 to 2040.

    Args:
        tmp_path: Run folder.
        grid_by_year: AAA's grid price per year (USD/kWh).
        site_ceiling: The region's site hydrogen ceiling in every year (USD/kg).

    Returns:
        The environment: the cell's LCOE falls from 60 USD/MWh in 2025 to 48 in 2028 and 40 in 2035; the
        electrolyser uses 0.05 MWh/kg at 1.0 USD/kg up to 2028 and less, at lower cost, after it.
    """
    years = [Year(y) for y in range(2025, 2041)]
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2035),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        geo_config=GeoConfig(included_power_mix="85% baseload + 15% grid"),
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,DRI\nDRI,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    env.year = Year(2025)
    env.input_costs = {"AAA": {year: {"electricity": grid_by_year[int(year)], "natural_gas": 0.03} for year in years}}
    env.hydrogen_efficiency = {year: 0.05 if year <= 2028 else 0.045 for year in years}
    env.hydrogen_capex_opex = {"AAA": {year: 1.0 if year <= 2028 else 0.7 for year in years}}
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
    env.initiate_baseload_lcoe_by_geo_key({year: {"AAA": 0.05} for year in years})
    env.initiate_capped_hydrogen_costs_by_year()
    env.initiate_baseload_lcoe_grid(
        xr.DataArray(
            np.array([[[60.0]], [[48.0]], [[40.0]]], dtype=np.float32),
            coords={"year": [2025, 2028, 2035], "lat": [0.0], "lon": [0.0]},
            dims=("year", "lat", "lon"),
            name="lcoe",
        )
    )
    env.initiate_site_hydrogen_ceilings({year: {"TestRegion": site_ceiling} for year in years}, {"AAA": "TestRegion"})
    return env


def _operating_prices(env: Environment, captive: CaptiveEnergy, years: range) -> list[dict[str, float]]:
    """The unsubsidised prices the NPV's score series reads for a candidate in each operating year."""
    return [env.candidate_energy_costs_for_year(LOCATION, "DRI", Year(year), captive=captive)[0] for year in years]


def test_captive_site_is_valued_at_its_construction_start_lcoe_with_a_flat_electrolyser(tmp_path: Path) -> None:
    """A site found in 2025 and built from 2028 pays 0.85 × L(2028) + 0.15 × g, and e(2028) and k(2028), every year.

    Notes:
        The cell's LCOE falls from 60 to 48 USD/MWh between the selection year and the construction start, and
        the grid is flat at 0.08 USD/kWh. Neither the later LCOE nor the later, cheaper electrolyser reaches the
        operating years 2032 to 2051.
    """
    env = _make_captive_env(tmp_path, {y: 0.08 for y in range(2025, 2041)}, site_ceiling=10.0)

    captive = env.captive_energy_at(LOCATION, Year(2028))
    prices = _operating_prices(env, captive, range(2032, 2052))

    electricity = 0.85 * 0.048 + 0.15 * 0.08
    assert captive.lcoe == pytest.approx(0.048)
    assert captive.hydrogen_contract is None
    assert [p["electricity"] for p in prices] == pytest.approx([electricity] * 20)
    assert [p["hydrogen"] for p in prices] == pytest.approx([(0.05 * 1000 * electricity + 1.0) * 1000] * 20)


def test_captive_site_above_the_ceiling_is_valued_at_the_ceiling_flat(tmp_path: Path) -> None:
    """A site whose own price in 2028 (3.64 USD/kg) is above the ceiling of 2028 (3.0) pays 3,000 USD/t for life."""
    env = _make_captive_env(tmp_path, {y: 0.08 + 0.005 * max(y - 2028, 0) for y in range(2025, 2041)}, 3.0)

    captive = env.captive_energy_at(LOCATION, Year(2028))
    prices = _operating_prices(env, captive, range(2032, 2052))

    assert captive.hydrogen_contract == pytest.approx(3.0)
    assert [p["hydrogen"] for p in prices] == pytest.approx([3000.0] * 20)


def test_captive_site_electricity_moves_by_the_grid_share_of_a_rising_grid(tmp_path: Path) -> None:
    """A grid rising by 0.005 USD/kWh a year moves the site's electricity by 0.15 × 0.005 a year, its hydrogen with it."""
    grid = {y: 0.08 + 0.005 * max(y - 2028, 0) for y in range(2025, 2041)}
    env = _make_captive_env(tmp_path, grid, site_ceiling=10.0)

    captive = env.captive_energy_at(LOCATION, Year(2028))
    prices = _operating_prices(env, captive, range(2032, 2041))

    steps = [later["electricity"] - earlier["electricity"] for earlier, later in zip(prices, prices[1:])]
    assert steps == pytest.approx([0.15 * 0.005] * 8)
    assert [p["hydrogen"] for p in prices] == pytest.approx(
        [(0.05 * 1000 * (0.85 * 0.048 + 0.15 * grid[y]) + 1.0) * 1000 for y in range(2032, 2041)]
    )


def test_captive_score_series_differs_from_a_stack_locked_at_the_selection_year(tmp_path: Path) -> None:
    """The NPV's score series priced from the stack of 2028 equals its prices' scores, not those of a 2025 stack."""
    env = _make_captive_env(tmp_path, {y: 0.08 for y in range(2025, 2041)}, site_ceiling=10.0)
    env.initiate_dynamic_feedstocks(
        [_feedstock("natural_gas", {"natural_gas": 10.0}), _feedstock("hydrogen", {"hydrogen": 0.055})]
    )
    env.initiate_technology_emission_factors([_ef("natural_gas", 1.4), _ef("hydrogen", 0.1)])
    env.carbon_costs = {"AAA": {Year(y): 300.0 for y in range(2025, 2041)}}  # hydrogen wins, so its price counts
    captive = env.captive_energy_at(LOCATION, Year(2028))

    series = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2032), Year(2040), captive=captive)

    assert series.picks == ["hydrogen"] * 8
    assert series.scores == _reference_series(env, LOCATION, "DRI", SHARES, 2032, 2040, captive=captive)[0]
    at_selection = env.reductant_score_series(
        LOCATION, "DRI", SHARES, Year(2032), Year(2040), captive=env.captive_energy_at(LOCATION, Year(2025))
    )
    assert series.scores != pytest.approx(at_selection.scores)


def test_captive_stack_raises_for_a_cell_without_a_finite_lcoe(tmp_path: Path) -> None:
    """A site whose cell has no finite LCOE in its lock year cannot lock its power cost and stops the run."""
    env = _make_captive_env(tmp_path, {y: 0.08 for y in range(2025, 2041)}, site_ceiling=10.0)
    assert env.baseload_lcoe_grid is not None
    env.baseload_lcoe_grid.values[1, 0, 0] = np.nan

    with pytest.raises(ValueError, match="No finite baseload LCOE"):
        env.captive_energy_at(LOCATION, Year(2028))


def test_captive_stack_raises_for_a_site_without_an_electrolyser_cost_row(tmp_path: Path) -> None:
    """A site whose geo key and country have no electrolyser cost row stops the run."""
    env = _make_captive_env(tmp_path, {y: 0.08 for y in range(2025, 2041)}, site_ceiling=10.0)
    env.hydrogen_capex_opex = {"BBB": env.hydrogen_capex_opex["AAA"]}

    with pytest.raises(ValueError, match="No electrolyser cost row"):
        env.captive_energy_at(LOCATION, Year(2028))


def test_captive_stack_holds_the_last_data_year_past_the_horizon(tmp_path: Path) -> None:
    """A lock year past the electrolyser, LCOE and ceiling data takes their last year's values."""
    env = _make_captive_env(tmp_path, {y: 0.08 for y in range(2025, 2041)}, site_ceiling=10.0)

    assert env.captive_energy_at(LOCATION, Year(2043)) == CaptiveEnergy(Year(2043), 0.85, 0.040, 0.045, 0.7, None)


def test_captive_stack_under_grid_only_has_no_baseload_share(tmp_path: Path) -> None:
    """Under "Grid only" the stack carries coverage 0 and LCOE 0, so the site pays the grid for its power."""
    env = _make_env(tmp_path)
    env.initiate_site_hydrogen_ceilings({year: {"TestRegion": 10.0} for year in YEARS}, {"AAA": "TestRegion"})

    captive = env.captive_energy_at(LOCATION, Year(2027))

    assert (captive.coverage, captive.lcoe) == (0.0, 0.0)
    assert captive.electricity(0.07) == pytest.approx(0.07)
