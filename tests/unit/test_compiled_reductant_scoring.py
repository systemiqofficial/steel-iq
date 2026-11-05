"""The compiled reductant scorer gives exactly what scoring the raw business cases gives."""

import random
from collections import defaultdict
from pathlib import Path

import pytest

from steelo.domain.calculate_costs import (
    compile_reductant_business_cases,
    score_compiled_reductant_business_cases,
    score_reductants_for_business_cases,
)
from steelo.domain.models import CaptiveEnergy, PrimaryFeedstock
from test_reductant_score_series import LOCATION, SHARES, Year, _ef, _feedstock, _make_env

CARRIERS = ["electricity", "natural_gas", "hydrogen", "coke", "bio_pci", "steam"]
EF_TABLE = {
    (tech, red, charge): 0.1 * i
    for i, (tech, red, charge) in enumerate(
        (t, r, c)
        for t in ("dri", "bof")
        for r in ("natural_gas", "hydrogen", "coke")
        for c in ("IO_low", "IO_mid", "scrap")
    )
}


def _business_cases() -> list[PrimaryFeedstock]:
    """Rows that exercise merging, non-energy secondary feedstock, outputs and rows without energy."""
    cases = []
    for tech in ("dri", "bof"):
        for charge in ("IO_low", "IO_mid", "scrap"):
            for reductant in ("natural_gas", "hydrogen", "coke"):
                pf = PrimaryFeedstock(metallic_charge=charge, reductant=reductant, technology=tech)
                pf.required_quantity_per_ton_of_product = 1.5 if charge != "scrap" else 1.1
                pf.add_energy_requirement("Natural Gas", 3.0)  # raw name
                pf.add_energy_requirement("natural_gas", 1.0)  # same carrier after normalising
                pf.add_energy_requirement("bio-pci", 0.5)
                pf.add_energy_requirement("unlisted_carrier", 9.0)  # not an energy key: ignored
                pf.add_secondary_feedstock("steam", 0.2)  # energy key given as secondary feedstock
                pf.add_secondary_feedstock("Burnt Lime", 0.1)  # energy key, raw name
                pf.add_secondary_feedstock("flux", 0.3)  # material, goes to the profile
                pf.add_output("steelmaking_slag", 0.2)
                pf.add_output("bof_gas", 0.4)
                pf.add_carbon_output("co2_stored", 0.7)
                cases.append(pf)
    cases.append(PrimaryFeedstock(metallic_charge="IO_low", reductant="coke", technology="dri"))  # no energy
    return cases


def _prices(rng: random.Random, drop: str | None = None) -> dict[str, float]:
    prices = {carrier: rng.uniform(0.01, 5.0) for carrier in CARRIERS + ["burnt_lime"]}
    prices.pop(drop, None)
    return prices


def _assert_same(actual, expected) -> None:
    assert actual.energy_vopex_by_input == expected.energy_vopex_by_input
    assert actual.score_by_input == expected.score_by_input
    assert actual.material_profile_by_input == expected.material_profile_by_input
    assert {c: {r: dict(b) for r, b in rs.items()} for c, rs in actual.energy_breakdown_by_input.items()} == {
        c: {r: dict(b) for r, b in rs.items()} for c, rs in expected.energy_breakdown_by_input.items()
    }
    assert [list(v) for v in actual.score_by_input.values()] == [list(v) for v in expected.score_by_input.values()]


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("with_ef", [True, False])
def test_compiled_scores_equal_uncompiled_scores(seed, with_ef):
    rng = random.Random(seed)
    cases = _business_cases()
    compiled = compile_reductant_business_cases(cases)
    ef_by_key = EF_TABLE if with_ef else None
    for _ in range(6):  # the same compiled cases serve many price sets, like many years
        energy = _prices(rng, drop=rng.choice([None, "hydrogen", "bio_pci", "steam"]))
        output = {
            "steelmaking_slag": rng.uniform(-3, 3),
            "bof_gas": rng.uniform(0, 2),
            "co2_stored": rng.uniform(-9, 9),
        }
        carbon_price = rng.uniform(0, 300)
        disposal = frozenset({"steelmaking_slag"})
        expected = score_reductants_for_business_cases(cases, energy, output, carbon_price, ef_by_key, disposal)
        actual = score_compiled_reductant_business_cases(compiled, energy, output, carbon_price, ef_by_key, disposal)
        _assert_same(actual, expected)


def test_compiled_scoring_raises_on_a_missing_emission_factor_like_the_original():
    cases = _business_cases()
    incomplete = {key: value for key, value in EF_TABLE.items() if key != ("bof", "coke", "scrap")}
    args = ({"natural_gas": 1.0}, {}, 10.0, incomplete, None)
    with pytest.raises(KeyError):
        score_reductants_for_business_cases(cases, *args)
    with pytest.raises(KeyError):
        score_compiled_reductant_business_cases(compile_reductant_business_cases(cases), *args)


def test_compiled_result_does_not_share_state_between_calls():
    cases = _business_cases()
    compiled = compile_reductant_business_cases(cases)
    first = score_compiled_reductant_business_cases(compiled, {"electricity": 1.0}, {}, 0.0, EF_TABLE, None)
    first.material_profile_by_input["IO_low"].clear()
    first.energy_breakdown_by_input["IO_low"]["coke"]["steam"] = 99.0
    second = score_compiled_reductant_business_cases(compiled, {"electricity": 1.0}, {}, 0.0, EF_TABLE, None)
    expected = score_reductants_for_business_cases(cases, {"electricity": 1.0}, {}, 0.0, EF_TABLE, None)
    _assert_same(second, expected)
    assert isinstance(second.energy_breakdown_by_input["IO_low"]["coke"], defaultdict)


def test_environment_compiles_once_per_list_and_recompiles_when_the_list_changes(tmp_path: Path) -> None:
    env = _make_env(tmp_path, carbon_by_year={y: 50.0 for y in range(2025, 2031)})
    env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))
    first = env._compiled_reductant_cases["DRI"][1]
    env.reductant_score_series(
        LOCATION,
        "DRI",
        SHARES,
        Year(2026),
        Year(2030),
        captive=CaptiveEnergy(Year(2026), 0.85, 0.05, 0.05, 1.0, None),
    )
    assert env._compiled_reductant_cases["DRI"][1] is first  # new prices, same compiled cases

    business_cases = env.dynamic_feedstocks["dri"]
    business_cases.append(_feedstock("coke", {"coke": 1.0}))  # an added row
    recompiled = env._compiled_business_cases("DRI", business_cases)
    assert recompiled is not first
    assert len(recompiled) == len(first) + 1


def test_environment_drops_compiled_cases_when_feedstocks_are_reinitialised(tmp_path: Path) -> None:
    env = _make_env(tmp_path, carbon_by_year={y: 50.0 for y in range(2025, 2031)})
    before = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))
    env.initiate_dynamic_feedstocks(
        [_feedstock("natural_gas", {"natural_gas": 20.0}), _feedstock("hydrogen", {"hydrogen": 0.055})]
    )
    env.initiate_technology_emission_factors([_ef("natural_gas", 1.4), _ef("hydrogen", 0.1)])
    after = env.reductant_score_series(LOCATION, "DRI", SHARES, Year(2025), Year(2031))
    assert after.scores != before.scores  # the doubled gas requirement is priced in
