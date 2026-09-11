"""Tests for the decision-flow viewer's row packing (steelo.utilities.interactive.decision_flows)."""

import pandas as pd
import pytest

from steelo.utilities.interactive import decision_flows

MOTIONS_COLUMNS = [
    "year",
    "kind",
    "source",
    "plant_id",
    "furnace_group_id",
    "geo_key",
    "old_technology",
    "new_technology",
    "old_capacity_t",
    "new_capacity_t",
    "owner_id",
    "product",
    "reductant",
]


def sample_motions() -> pd.DataFrame:
    """A small motions table covering a pipeline arrival, a close and an expansion."""
    rows = [
        [
            2025,
            "pipeline",
            "input_data",
            "P1",
            "P1_0",
            "BGD",
            None,
            "DRI",
            None,
            2_200_000.0,
            "E1",
            "iron",
            "natural_gas",
        ],
        [2026, "close", "pam", "P2", "P2_0", "CHN:CN-HE", "BF", None, 1_000_000.0, None, "E2", "iron", None],
        [2027, "expansion", "pam", "P3", "P3_1", "BRA", None, "EAF", None, 500_000.0, "E3", "steel", None],
    ]
    return pd.DataFrame(rows, columns=MOTIONS_COLUMNS)


def test_pack_motions_compacts_rows() -> None:
    """Rows keep year, kind and geo_key; capacities become Mt with three decimals."""
    packed = decision_flows.pack_motions(sample_motions())

    assert [row["kind"] for row in packed] == ["pipeline", "close", "expansion"]
    assert packed[0] == {
        "fg": "P1_0",
        "year": 2025,
        "kind": "pipeline",
        "ot": None,
        "nt": "DRI",
        "om": None,
        "nm": 2.2,
        "geo": "BGD",
    }
    assert packed[1]["om"] == 1.0 and packed[1]["nm"] is None


def test_pack_motions_rejects_unknown_kind() -> None:
    """A motion kind without a band fails loudly instead of vanishing from the chart."""
    motions = sample_motions()
    motions.loc[0, "kind"] = "teleport"

    with pytest.raises(ValueError, match="teleport"):
        decision_flows.pack_motions(motions)


def test_chart_config_bands_every_kind() -> None:
    """The viewer config names a band for every motion kind and lists the new-build kinds."""
    assert set(decision_flows.CHART_CONFIG["kindToGroup"]) == set(decision_flows.KIND_TO_GROUP)
    assert decision_flows.CHART_CONFIG["newBuildKinds"] == ["expansion", "greenfield", "pipeline"]
    assert all(group in decision_flows.GROUP_ORDER for group in decision_flows.KIND_TO_GROUP.values())
