"""Tests for the capacity-pool chart set: data shaping and the end-to-end draw.

The shaping functions are checked against hand-written artefact CSVs so the
sums, signs and orderings are pinned; ``plot_all`` is smoke-tested through to
files on disk, because the charts run unattended at the end of every policy-ON
simulation and a silent save failure would lose the run's only policy view.
"""

import csv
from pathlib import Path

import pytest

from steelo.capacity_policy import plotter
from steelo.capacity_policy.recorder import (
    GATE_DECISIONS_COLUMNS,
    GATE_DECISIONS_FILE,
    LEDGER_COLUMNS,
    LEDGER_FILE,
    MOTIONS_COLUMNS,
    MOTIONS_FILE,
    STATE_COLUMNS,
    STATE_FILE,
)
from steelo.domain.models import PlotPaths
from steelo.utilities.steeliq_plotter import PlotConfig

MT = 1e6


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def policy_dir(tmp_path: Path) -> Path:
    """A flushed policy artefact set covering every charted vocabulary item."""
    directory = tmp_path / "policy"
    directory.mkdir()
    write_csv(
        directory / LEDGER_FILE,
        LEDGER_COLUMNS,
        [
            {"year": 2019, "operation": "seed", "amount_t": 30 * MT, "region_tag": "cluster_a", "product": "steel"},
            {"year": 2025, "operation": "deposit_close", "amount_t": 10 * MT, "product": "steel"},
            {"year": 2025, "operation": "deposit_close_end_of_life", "amount_t": 5 * MT, "product": "steel"},
            {"year": 2026, "operation": "deposit_replace", "amount_t": 3 * MT, "product": "steel"},
            {"year": 2026, "operation": "withdraw_expansion", "amount_t": 4 * MT, "product": "steel"},
            {"year": 2026, "operation": "withdraw_greenfield", "amount_t": 2 * MT, "product": "iron"},
            {
                "year": 2026,
                "operation": "blocked_greenfield",
                "amount_t": 6 * MT,
                "product": "steel",
                "blocked_reason": "no_single_owner_with_sufficient_credits",
            },
            {
                "year": 2025,
                "operation": "blocked_expansion",
                "amount_t": 1 * MT,
                "product": "steel",
                "blocked_reason": "insufficient_applicable_pool",
            },
        ],
    )
    write_csv(
        directory / STATE_FILE,
        STATE_COLUMNS,
        [
            {
                "year": 2025,
                "region_tag": "cluster_a",
                "owner_id": "owner_1",
                "product": "steel",
                "remaining_t": 30 * MT,
                "oldest_vintage": 2019,
            },
            {
                "year": 2025,
                "region_tag": "",
                "owner_id": "",
                "product": "steel",
                "remaining_t": 15 * MT,
                "oldest_vintage": 2025,
            },
            {
                "year": 2026,
                "region_tag": "cluster_a",
                "owner_id": "owner_1",
                "product": "steel",
                "remaining_t": 26 * MT,
                "oldest_vintage": 2019,
            },
            {
                "year": 2026,
                "region_tag": "",
                "owner_id": "",
                "product": "iron",
                "remaining_t": 8 * MT,
                "oldest_vintage": 2026,
            },
        ],
    )
    write_csv(
        directory / MOTIONS_FILE,
        MOTIONS_COLUMNS,
        [
            {"year": 2025, "kind": "close", "old_technology": "BF-BOF", "old_capacity_t": 10 * MT},
            {"year": 2025, "kind": "switch", "new_technology": "EAF", "new_capacity_t": 7 * MT},
            {"year": 2026, "kind": "expansion", "new_technology": "EAF", "new_capacity_t": 4 * MT},
            {"year": 2026, "kind": "greenfield", "new_technology": "DRI-EAF", "new_capacity_t": 2 * MT},
            {"year": 2026, "kind": "pipeline", "new_technology": "DRI-EAF", "new_capacity_t": 3 * MT},
            {"year": 2026, "kind": "renovate", "old_technology": "BF-BOF", "old_capacity_t": 5 * MT},
        ],
    )
    write_csv(
        directory / GATE_DECISIONS_FILE,
        GATE_DECISIONS_COLUMNS,
        [
            {"year": 2025, "decision": "ratio", "ratio": 1.5, "capacity_t": 9 * MT},
            {"year": 2026, "decision": "blocked_utilization", "ratio": "", "capacity_t": 12 * MT},
        ],
    )
    return directory


def test_load_artefacts_returns_none_without_artefacts(tmp_path: Path) -> None:
    """None for a policy-OFF run: no directory given, or a directory the flush never wrote to."""
    assert plotter.load_artefacts(None) is None
    assert plotter.load_artefacts(tmp_path / "absent") is None
    assert plotter.load_artefacts(tmp_path) is None


def test_load_artefacts_reads_years_from_state(policy_dir: Path) -> None:
    """The chart axis is the state file's year range, not the ledger's seed vintages."""
    art = plotter.load_artefacts(policy_dir)

    assert art is not None
    assert art.years == [2025, 2026]
    assert art.products == ["iron", "steel"]
    assert art.clusters == ["cluster_a"]


def test_load_artefacts_rejects_unknown_ledger_operation(policy_dir: Path) -> None:
    """New recorder vocabulary must be taught to the charts, not silently dropped from sums."""
    with (policy_dir / LEDGER_FILE).open("a", newline="") as handle:
        csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS).writerow(
            {"year": 2026, "operation": "withdraw_wormhole", "amount_t": 1 * MT}
        )

    with pytest.raises(ValueError, match="withdraw_wormhole"):
        plotter.load_artefacts(policy_dir)


def test_pool_by_tag_partitions_credit_per_product(policy_dir: Path) -> None:
    art = plotter.load_artefacts(policy_dir)
    assert art is not None

    frames = plotter.pool_by_tag(art)

    assert frames["steel"] == {"cluster_a": [30.0, 26.0], "untagged": [15.0, 0.0]}
    assert frames["iron"] == {"untagged": [0.0, 8.0]}


def test_applicable_pool_nests_rather_than_stacks(policy_dir: Path) -> None:
    """The whole pool is the sum; each cluster's line is its ring-fenced slice of it."""
    art = plotter.load_artefacts(policy_dir)
    assert art is not None

    total, clusters = plotter.applicable_pool(art)["steel"]

    assert total == [45.0, 26.0]
    assert clusters == {"cluster_a": [30.0, 26.0]}


def test_policy_bite_merges_ledger_blocks_and_utilisation_gate(policy_dir: Path) -> None:
    art = plotter.load_artefacts(policy_dir)
    assert art is not None

    reasons = plotter.policy_bite(art)

    assert reasons == {
        "Pool short of applicable credit": [1.0, 0.0],
        "No single holder covers a greenfield": [0.0, 6.0],
        "Utilisation gate (replacement refused)": [0.0, 12.0],
    }


def test_annual_flows_split_deposits_from_withdrawals(policy_dir: Path) -> None:
    """Both sides positive and seed excluded — the chart applies the sign, not the data."""
    art = plotter.load_artefacts(policy_dir)
    assert art is not None

    deposits, withdrawals = plotter.annual_flows(art)

    assert deposits == {
        "Deposit — closure": [10.0, 0.0],
        "Deposit — end-of-life retirement": [5.0, 0.0],
        "Deposit — penalised replacement": [0.0, 3.0],
    }
    assert withdrawals == {
        "Withdrawal — expansion": [0.0, 4.0],
        "Withdrawal — greenfield": [0.0, 2.0],
    }


def test_technology_mix_by_kind_splits_builds_and_ranks_by_total(policy_dir: Path) -> None:
    """Closes and renovations move no capacity into a technology, so they stay out."""
    art = plotter.load_artefacts(policy_dir)
    assert art is not None

    mix = plotter.technology_mix_by_kind(art)

    assert list(mix) == ["EAF", "DRI-EAF"]
    assert mix["EAF"] == {"Switch": 7.0, "Expansion": 4.0}
    assert mix["DRI-EAF"] == {"Greenfield": 2.0, "Pipeline": 3.0}


def test_plot_all_writes_the_full_chart_set(policy_dir: Path, tmp_path: Path) -> None:
    """End to end: every chart and its CSV land under plots_dir/capacity_pool."""
    plots_dir = tmp_path / "plots"
    pool_plotter = plotter.CapacityPoolPlotter(
        config=PlotConfig(default_dpi=72), plot_paths=PlotPaths(plots_dir=plots_dir)
    )

    saved = pool_plotter.plot_all(policy_dir)

    out_dir = plots_dir / "capacity_pool"
    expected = {
        "1d_applicable_pool_iron.png",
        "1d_applicable_pool_steel.png",
        "1e_pool_area_iron.png",
        "1e_pool_area_steel.png",
        "2_policy_bite.png",
        "3_annual_flows.png",
        "4b_technology_mix.png",
    }
    assert {path.name for path in saved} == expected
    assert {path.name for path in out_dir.glob("*.png")} == expected
    assert {path.name for path in out_dir.glob("*.csv")} == {name.replace(".png", ".csv") for name in expected}


def test_plot_all_draws_nothing_for_a_policy_off_run(tmp_path: Path) -> None:
    plots_dir = tmp_path / "plots"
    pool_plotter = plotter.CapacityPoolPlotter(config=PlotConfig(), plot_paths=PlotPaths(plots_dir=plots_dir))

    assert pool_plotter.plot_all(tmp_path / "absent") == []
    assert not (plots_dir / "capacity_pool").exists()
