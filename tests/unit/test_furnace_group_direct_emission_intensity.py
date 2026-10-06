"""Tests for the utilisation-independent direct emission intensity and trade carbon cost of furnace groups."""

from pathlib import Path

import pytest

from steelo.domain.models import (
    Environment,
    FurnaceGroup,
    Location,
    Plant,
    PointInTime,
    PrimaryFeedstock,
    Technology,
    TechnologyEmissionFactors,
    TimeFrame,
    Volumes,
    Year,
)
from steelo.simulation import SimulationConfig

BOUNDARY = "rs-inspired"
EF_BY_KEY = {("bf", "coke", "IO_low"): 2.0, ("bf", "coke", "IO_mid"): 1.0}


def _feedstock(charge: str, required_quantity: float | None) -> PrimaryFeedstock:
    feedstock = PrimaryFeedstock(metallic_charge=charge, reductant="coke", technology="BF")
    feedstock.required_quantity_per_ton_of_product = required_quantity
    return feedstock


def _furnace_group(
    fg_id: str = "plant1_fg0",
    bill_of_materials: dict | None = None,
    utilization_rate: float = 0.8,
    feedstocks: list[PrimaryFeedstock] | None = None,
    status: str = "operating",
    technology_name: str = "BF",
    capacity: float = 1000.0,
) -> FurnaceGroup:
    """Blast furnace on coke with io_low (1.4 t/t) and io_mid (1.5 t/t) business cases."""
    if feedstocks is None:
        feedstocks = [_feedstock("IO_low", 1.4), _feedstock("IO_mid", 1.5)]
    technology = Technology(
        name=technology_name,
        product="iron",
        bill_of_materials=None,
        capex_type="greenfield",
        dynamic_business_case=feedstocks,
    )
    return FurnaceGroup(
        furnace_group_id=fg_id,
        technology=technology,
        capacity=Volumes(capacity),
        lifetime=PointInTime(
            plant_lifetime=20,
            current=2025,
            time_frame=TimeFrame(start=Year(2025), end=Year(2045)),
        ),
        status=status,
        chosen_reductant="coke",
        last_renovation_date=None,
        historical_production={},
        utilization_rate=utilization_rate,
        bill_of_materials=bill_of_materials,
    )


def _bom(demand_share_by_charge: dict[str, float]) -> dict:
    return {
        "materials": {charge: {"demand_share_pct": share} for charge, share in demand_share_by_charge.items()},
        "energy": {},
    }


def _emission_factor(charge: str, factor: float, boundary: str = BOUNDARY) -> TechnologyEmissionFactors:
    return TechnologyEmissionFactors(
        business_case=f"{charge}_coke_BF",
        technology="BF",
        boundary=boundary,
        metallic_charge=charge,
        reductant="coke",
        direct_ghg_factor=factor,
        direct_with_biomass_ghg_factor=factor,
        indirect_ghg_factor=0.0,
    )


def _plant(plant_id: str, iso3: str, furnace_groups: list[FurnaceGroup]) -> Plant:
    return Plant(
        plant_id=plant_id,
        location=Location(lat=50.0, lon=10.0, iso3=iso3, country="Test", region="Test"),
        furnace_groups=furnace_groups,
        technology_unit_fopex={},
        power_source="grid",
        soe_status="private",
        parent_gem_id="E100000000000",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
    )


def test_intensity_from_bom_product_shares():
    """BOM demand shares divided by required quantity give product shares that weight the factors."""
    fg = _furnace_group(bill_of_materials=_bom({"io_low": 0.7, "io_mid": 0.75}))

    # io_low: 0.7 / 1.4 = 0.5 of product; io_mid: 0.75 / 1.5 = 0.5 of product
    assert fg.direct_emission_intensity(EF_BY_KEY) == pytest.approx(0.5 * 2.0 + 0.5 * 1.0)


def test_intensity_counts_only_feedstocks_charged_in_bom():
    """A business-case feedstock absent from the BOM carries no emissions."""
    fg = _furnace_group(bill_of_materials=_bom({"io_low": 1.4}))

    assert fg.direct_emission_intensity(EF_BY_KEY, fallback_input_shares={"io_mid": 1.0}) == pytest.approx(2.0)


def test_intensity_falls_back_to_fleet_average_input_shares():
    """Without BOM shares the fleet-average input shares are converted to product shares and renormalised."""
    fg = _furnace_group(bill_of_materials=None)

    raw_low, raw_mid = 0.6 / 1.4, 0.4 / 1.5
    share_low, share_mid = raw_low / (raw_low + raw_mid), raw_mid / (raw_low + raw_mid)
    expected = share_low * 2.0 + share_mid * 1.0
    assert fg.direct_emission_intensity(EF_BY_KEY, fallback_input_shares={"io_low": 0.6, "io_mid": 0.4}) == (
        pytest.approx(expected)
    )


def test_intensity_falls_back_to_equal_shares():
    """Without BOM shares or overlapping fleet-average shares every feedstock gets an equal share."""
    fg = _furnace_group(bill_of_materials={"materials": {}, "energy": {}})

    assert fg.direct_emission_intensity(EF_BY_KEY) == pytest.approx(1.5)
    assert fg.direct_emission_intensity(EF_BY_KEY, fallback_input_shares={"scrap": 1.0}) == pytest.approx(1.5)


def test_intensity_is_independent_of_utilisation():
    """The intensity depends on factors and shares only, unlike the realised carbon cost per unit."""
    bom = _bom({"io_low": 0.7, "io_mid": 0.75})
    idle = _furnace_group(fg_id="idle", bill_of_materials=bom, utilization_rate=0.0)
    busy = _furnace_group(fg_id="busy", bill_of_materials=bom, utilization_rate=0.9)

    assert idle.direct_emission_intensity(EF_BY_KEY) == busy.direct_emission_intensity(EF_BY_KEY)
    assert idle.carbon_cost_per_unit == 0.0


def test_intensity_raises_on_missing_factor_or_feedstocks():
    """A missing emission factor and an empty business case both fail loudly."""
    fg = _furnace_group(bill_of_materials=_bom({"io_low": 0.7, "io_mid": 0.75}))
    with pytest.raises(KeyError):
        fg.direct_emission_intensity({("bf", "coke", "IO_low"): 2.0})

    without_feedstocks = _furnace_group(feedstocks=[])
    with pytest.raises(ValueError, match="no effective primary feedstocks"):
        without_feedstocks.direct_emission_intensity(EF_BY_KEY)

    without_quantity = _furnace_group(feedstocks=[_feedstock("IO_low", None)], bill_of_materials=_bom({"io_low": 1.4}))
    with pytest.raises(ValueError, match="required quantity"):
        without_quantity.direct_emission_intensity(EF_BY_KEY)


def _environment(tmp_path: Path) -> Environment:
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF\nBF,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    env.year = Year(2026)
    return env


def test_update_trade_carbon_costs_uses_plant_series_price(tmp_path: Path):
    """The environment prices each furnace group's intensity at its plant's carbon cost series for the year."""
    env = _environment(tmp_path)
    env.technology_emission_factors = [
        _emission_factor("IO_low", 2.0),
        _emission_factor("IO_mid", 1.0),
        _emission_factor("IO_low", 99.0, boundary="other-boundary"),
    ]
    env.avg_boms = {
        "BF": {
            "io_low": {"unit_cost": 100.0, "input_share_pct": 0.6},
            "io_mid": {"unit_cost": 90.0, "input_share_pct": 0.4},
        }
    }

    priced_fg = _furnace_group(fg_id="priced", bill_of_materials=_bom({"io_low": 0.7, "io_mid": 0.75}))
    priced_plant = _plant("priced_plant", "DEU", [priced_fg])
    priced_plant.set_carbon_cost_series({Year(2026): 50.0})
    unpriced_fg = _furnace_group(fg_id="unpriced", bill_of_materials=None)
    unpriced_plant = _plant("unpriced_plant", "IND", [unpriced_fg])

    env.update_trade_carbon_costs_of_furnace_groups([priced_plant, unpriced_plant])

    assert priced_fg.trade_emission_intensity == pytest.approx(1.5)
    assert priced_fg.trade_carbon_cost_per_unit == pytest.approx(75.0)
    raw_low, raw_mid = 0.6 / 1.4, 0.4 / 1.5
    expected_intensity = (raw_low * 2.0 + raw_mid * 1.0) / (raw_low + raw_mid)
    assert unpriced_fg.trade_emission_intensity == pytest.approx(expected_intensity)
    assert unpriced_fg.trade_carbon_cost_per_unit == 0.0


def test_update_trade_carbon_costs_skips_inactive_furnace_groups(tmp_path: Path):
    """A closed furnace group never enters the trade LP, so bad business-case data on it must not raise."""
    env = _environment(tmp_path)
    env.technology_emission_factors = [_emission_factor("IO_low", 2.0), _emission_factor("IO_mid", 1.0)]
    env.avg_boms = {}
    active_fg = _furnace_group(fg_id="active", bill_of_materials=_bom({"io_low": 0.7, "io_mid": 0.75}))
    closed_fg = _furnace_group(fg_id="closed", feedstocks=[], status="closed")
    closed_fg.trade_emission_intensity = 3.0

    env.update_trade_carbon_costs_of_furnace_groups([_plant("plant", "DEU", [active_fg, closed_fg])])

    assert active_fg.trade_emission_intensity == pytest.approx(1.5)
    assert closed_fg.trade_emission_intensity == 3.0


def _observed(fg: FurnaceGroup, intensity: float, paid: float, year: int = 2025) -> FurnaceGroup:
    fg.upstream_emission_intensity = intensity
    fg.upstream_carbon_cost_paid = paid
    fg.upstream_carbon_year = year
    return fg


def test_fill_missing_upstream_uses_country_then_global_technology_peers(tmp_path: Path):
    """Unobserved active furnace groups take the capacity-weighted mean of observed same-technology peers."""
    env = _environment(tmp_path)
    chn_small = _observed(_furnace_group(fg_id="chn_small", technology_name="BOF", capacity=1000.0), 2.0, 20.0)
    chn_large = _observed(_furnace_group(fg_id="chn_large", technology_name="BOF", capacity=3000.0), 1.0, 0.0)
    chn_idle = _furnace_group(fg_id="chn_idle", technology_name="BOF")
    chn_stale = _observed(_furnace_group(fg_id="chn_stale", technology_name="BOF"), 0.1, 1.0, year=2020)
    ind_new = _furnace_group(fg_id="ind_new", technology_name="BOF")
    ind_eaf = _furnace_group(fg_id="ind_eaf", technology_name="EAF")
    deu_eaf = _observed(_furnace_group(fg_id="deu_eaf", technology_name="EAF", status="closed"), 0.5, 5.0)

    env.fill_missing_upstream_embedded_carbon(
        [
            _plant("chn", "CHN", [chn_small, chn_large, chn_idle, chn_stale]),
            _plant("ind", "IND", [ind_new, ind_eaf]),
            _plant("deu", "DEU", [deu_eaf]),
        ]
    )

    chn_mean = (pytest.approx((2.0 * 1000 + 1.0 * 3000) / 4000), pytest.approx(20.0 * 1000 / 4000))
    for fg in (chn_idle, chn_stale, ind_new):
        assert (fg.upstream_emission_intensity, fg.upstream_carbon_cost_paid) == chn_mean
    assert chn_stale.upstream_carbon_year == 2020
    assert (chn_small.upstream_emission_intensity, chn_large.upstream_emission_intensity) == (2.0, 1.0)
    # The only observed EAF is closed, so it is no peer and the Indian EAF stays at zero
    assert (ind_eaf.upstream_emission_intensity, ind_eaf.upstream_carbon_cost_paid) == (0.0, 0.0)
