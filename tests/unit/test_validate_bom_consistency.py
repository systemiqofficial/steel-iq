"""Tests for TM_PAM_connector.validate_bom_consistency's aggregated metallic-charge checks.

Minimum and maximum shares are checked independently, so a max-only constraint (the BOF
DRI ≤ 15% and HBI ≤ 15% rows of the current data) is still checked against its cap.
"""

from types import SimpleNamespace

from steelo.adapters.repositories.in_memory_repository import PlantInMemoryRepository
from steelo.domain.models import AggregatedMetallicChargeConstraint
from steelo.domain.trade_modelling.TM_PAM_connector import TM_PAM_connector


def _make_connector() -> TM_PAM_connector:
    return TM_PAM_connector(dynamic_feedstocks_classes={}, plants=PlantInMemoryRepository(), transport_kpis=None)


def _make_bof(demands: dict[str, float]) -> SimpleNamespace:
    """A producing BOF furnace group whose BOM materials carry the given demands (t), production 100 t."""
    return SimpleNamespace(
        furnace_group_id="fg_bof",
        technology=SimpleNamespace(name="BOF"),
        allocated_volumes=100.0,
        bill_of_materials={"materials": {name: {"demand": demand} for name, demand in demands.items()}},
        effective_primary_feedstocks=[],
    )


def _checks(issues: list[dict]) -> list[str]:
    return [issue["check"] for issue in issues]


def test_max_only_constraint_breached_is_flagged():
    fg = _make_bof({"hot_metal": 70.0, "dri_low": 30.0})
    constraints = [AggregatedMetallicChargeConstraint("BOF", "DRI", minimum_share=0.0, maximum_share=0.15)]

    issues = _make_connector().validate_bom_consistency([fg], aggregated_constraints=constraints)

    assert _checks(issues) == ["aggregated_max_share"]
    assert issues[0]["actual_share"] == 0.30
    assert issues[0]["required_share"] == 0.15


def test_max_only_constraint_within_cap_passes():
    fg = _make_bof({"hot_metal": 85.0, "dri_low": 15.0})
    constraints = [AggregatedMetallicChargeConstraint("BOF", "DRI", minimum_share=0.0, maximum_share=0.15)]

    issues = _make_connector().validate_bom_consistency([fg], aggregated_constraints=constraints)

    assert issues == []


def test_min_only_constraint_breached_is_flagged():
    fg = _make_bof({"hot_metal": 50.0, "scrap": 50.0})
    constraints = [AggregatedMetallicChargeConstraint("BOF", "hot_metal", minimum_share=0.7, maximum_share=None)]

    issues = _make_connector().validate_bom_consistency([fg], aggregated_constraints=constraints)

    assert _checks(issues) == ["aggregated_min_share"]


def test_dri_and_hbi_are_capped_separately():
    """10% DRI plus 10% HBI breaches neither 15% cap: hot and cold forms are not combined."""
    fg = _make_bof({"hot_metal": 80.0, "dri_low": 10.0, "hbi_low": 10.0})
    constraints = [
        AggregatedMetallicChargeConstraint("BOF", "DRI", minimum_share=0.0, maximum_share=0.15),
        AggregatedMetallicChargeConstraint("BOF", "HBI", minimum_share=0.0, maximum_share=0.15),
    ]

    issues = _make_connector().validate_bom_consistency([fg], aggregated_constraints=constraints)

    assert issues == []


def test_constraint_of_another_technology_is_ignored():
    fg = _make_bof({"hot_metal": 70.0, "dri_low": 30.0})
    constraints = [AggregatedMetallicChargeConstraint("EAF", "DRI", minimum_share=0.0, maximum_share=0.15)]

    issues = _make_connector().validate_bom_consistency([fg], aggregated_constraints=constraints)

    assert issues == []
