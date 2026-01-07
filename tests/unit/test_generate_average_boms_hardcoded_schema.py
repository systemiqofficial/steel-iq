from __future__ import annotations

from pathlib import Path

import pytest

from steelo.domain.constants import Year
from steelo.domain.models import (
    Environment,
    FallbackMaterialCost,
    FurnaceGroup,
    Location,
    Plant,
    PointInTime,
    PrimaryFeedstock,
    Technology,
    TimeFrame,
)
from steelo.simulation import SimulationConfig
from steelo.simulation_types import get_default_technology_settings


def _make_env(tmp_path: Path) -> Environment:
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text(
        "Origin,BF,BOF,DRI,EAF\nBF,NO,NO,NO,NO\nBOF,NO,NO,YES,YES\nDRI,NO,NO,NO,NO\nEAF,NO,NO,NO,NO\n"
    )
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path=tmp_path / "master.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
    )
    return Environment(config=config, tech_switches_csv=tech_switches_csv)


def test_hardcoded_avg_bom_includes_demand_share_pct(tmp_path: Path) -> None:
    env = _make_env(tmp_path)
    env.fallback_material_costs = [
        FallbackMaterialCost(
            iso3="AAA",
            technology="BF_CHARCOAL",
            metric="Fallback material cost",
            unit="USD/t",
            costs_by_year={Year(2025): 123.0},
        )
    ]
    env.default_metallic_charge_per_technology = {"BF_CHARCOAL": "io_high"}

    charcoal_feedstock = PrimaryFeedstock(metallic_charge="io_high", reductant="charcoal", technology="BF_CHARCOAL")
    charcoal_feedstock.required_quantity_per_ton_of_product = 1.0
    charcoal_feedstock.add_energy_requirement("electricity", 1.0)
    env.dynamic_feedstocks = {"BF_CHARCOAL": [charcoal_feedstock]}

    eaf = Technology(name="EAF", product="steel")
    eaf_fg = FurnaceGroup(
        furnace_group_id="fg-eaf",
        capacity=100.0,
        status="operating",
        last_renovation_date=None,
        technology=eaf,
        historical_production={},
        utilization_rate=1.0,
        lifetime=PointInTime(
            plant_lifetime=20,
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2025), end=Year(2030)),
        ),
        bill_of_materials={
            "materials": {
                "scrap": {"demand": 100.0, "total_material_cost": 1_000.0},
            }
        },
    )
    plant = Plant(
        plant_id="plant-1",
        location=Location(lat=0.0, lon=0.0, country="Test", region="Test", iso3="TST"),
        furnace_groups=[eaf_fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="unknown",
        workforce_size=0,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={},
    )

    env.generate_average_boms([plant], iso3=None)

    assert env.avg_boms["BF_CHARCOAL"]["io_high"]["demand_share_pct"] == pytest.approx(1.0)
    assert env.avg_boms["BF_CHARCOAL"]["io_high"]["unit_cost"] == pytest.approx(123.0)

    bom, _, _ = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 10.0},
        tech="BF_CHARCOAL",
        capacity=100.0,
    )
    assert bom is not None
    assert bom["materials"]["io_high"]["demand"] == pytest.approx(100.0)


def test_get_bom_from_avg_boms_defaults_missing_demand_share_pct_for_single_entry(tmp_path: Path) -> None:
    env = _make_env(tmp_path)

    charcoal_feedstock = PrimaryFeedstock(metallic_charge="io_high", reductant="charcoal", technology="BF_CHARCOAL")
    charcoal_feedstock.required_quantity_per_ton_of_product = 1.0
    charcoal_feedstock.add_energy_requirement("electricity", 1.0)
    env.dynamic_feedstocks = {"BF_CHARCOAL": [charcoal_feedstock]}

    env.avg_boms = {"BF_CHARCOAL": {"io_high": {"unit_cost": 123.0}}}

    bom, _, _ = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 10.0},
        tech="BF_CHARCOAL",
        capacity=100.0,
    )
    assert bom is not None
    assert bom["materials"]["io_high"]["demand"] == pytest.approx(100.0)
