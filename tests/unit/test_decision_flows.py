"""Tests for the decision-flow sankey writer (steelo.utilities.decision_flows)."""

import pandas as pd
import pytest

from steelo.utilities import decision_flows

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


def test_write_decision_flows_html_embeds_data(tmp_path) -> None:
    """The written HTML is self-contained: config, motions and plotly.js are inlined."""
    motions_csv = tmp_path / "pam_motions.csv"
    sample_motions().to_csv(motions_csv, index=False)
    output_path = tmp_path / "plots" / "PAM" / "decision_flows.html"

    written = decision_flows.write_decision_flows_html(
        motions_csv=motions_csv,
        output_path=output_path,
        run_title="sim_test",
    )

    assert written == output_path
    html = output_path.read_text()
    for placeholder in ("__PLOTLYJS__", "__CONFIG__", "__DATA__"):
        assert placeholder not in html
    assert "sim_test" in html
    assert '"Pipeline"' in html
    assert "P1_0" in html


def test_write_decision_flows_html_missing_csv_returns_none(tmp_path) -> None:
    """A run without a motions file skips the viewer instead of crashing the plot stage."""
    written = decision_flows.write_decision_flows_html(
        motions_csv=tmp_path / "absent.csv",
        output_path=tmp_path / "decision_flows.html",
        run_title="sim_test",
    )

    assert written is None
    assert not (tmp_path / "decision_flows.html").exists()
