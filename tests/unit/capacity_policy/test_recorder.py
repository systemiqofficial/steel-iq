"""Tests for the run artefacts: the four CSVs, and the arithmetic they must agree on.

The load-bearing claim is the reconciliation invariant — state(Y) equals the seed
plus every ledger flow stamped up to Y — checked from the written files alone,
because that is how anyone reading a run will check it. The second claim is that
emission is conditional on the binding: a disabled run leaves no policy files at
all, not four empty ones.
"""

import csv
import json
from pathlib import Path

import pytest

from steelo.capacity_policy import CapacityPolicyConfig, CapacityPolicyRecorder, CapacityPool, Credit
from steelo.capacity_policy import handlers as cp_handlers
from steelo.capacity_policy.bootstrap import configure_capacity_policy
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

POLICY_FILES = (LEDGER_FILE, STATE_FILE, MOTIONS_FILE, GATE_DECISIONS_FILE)


@pytest.fixture(autouse=True)
def unbind_after_test():
    """Module-level binding must never leak between tests."""
    yield
    cp_handlers.unbind_capacity_policy()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def credit(amount: float, vintage: int, *, tag: str | None = None, owner: str | None = None, product: str = "iron"):
    return Credit(amount_mt=amount, vintage_year=vintage, region_tag=tag, owner_id=owner, product=product)


class TestSchema:
    def test_writes_four_files_with_headers_when_nothing_happened(self, tmp_path):
        """An enabled run always leaves the same artefact set, data rows or not."""
        counts = CapacityPolicyRecorder().write_csvs(tmp_path)

        assert sorted(path.name for path in tmp_path.iterdir()) == sorted(POLICY_FILES)
        assert counts == {name: 0 for name in POLICY_FILES}
        for name, columns in (
            (LEDGER_FILE, LEDGER_COLUMNS),
            (STATE_FILE, STATE_COLUMNS),
            (MOTIONS_FILE, MOTIONS_COLUMNS),
            (GATE_DECISIONS_FILE, GATE_DECISIONS_COLUMNS),
        ):
            with (tmp_path / name).open() as handle:
                assert next(csv.reader(handle)) == list(columns)

    def test_every_ledger_operation_writes_a_row(self, tmp_path):
        recorder = CapacityPolicyRecorder()
        operations = (
            "seed",
            "deposit_close",
            "deposit_close_end_of_life",
            "deposit_replace",
            "withdraw_expansion",
            "withdraw_greenfield",
            "expired",
            "refunded",
            "blocked_expansion",
            "blocked_greenfield",
            "greenfield_discard",
        )
        for operation in operations:
            recorder.record_ledger(year=2026, operation=operation, amount_t=1.0, product="iron")

        recorder.write_csvs(tmp_path)

        assert [row["operation"] for row in read_rows(tmp_path / LEDGER_FILE)] == list(operations)

    def test_unknown_operation_kind_and_source_refuse(self):
        recorder = CapacityPolicyRecorder()
        with pytest.raises(ValueError, match="Unknown ledger operation"):
            recorder.record_ledger(year=2026, operation="withdraw", amount_t=1.0)
        with pytest.raises(ValueError, match="Unknown motion kind"):
            recorder.record_motion(
                year=2026, kind="build", source="pam", plant_id="p", furnace_group_id="fg", geo_key="CHN"
            )
        with pytest.raises(ValueError, match="Unknown motion source"):
            recorder.record_motion(
                year=2026, kind="close", source="destiny", plant_id="p", furnace_group_id="fg", geo_key="CHN"
            )

    def test_consumed_credits_round_trip_through_the_ledger(self, tmp_path):
        """The tag rides inside the tuple: a build may spend a tagged credit, so the
        request's own tag cannot reconstruct per-tag flows."""
        recorder = CapacityPolicyRecorder()
        recorder.record_ledger(
            year=2027,
            operation="withdraw_greenfield",
            amount_t=3.0,
            region_tag=None,
            owner_id="indi_CHN",
            product="iron",
            credits_consumed=(credit(2.0, 2019, tag="Yangtze", owner="E_a"), credit(1.0, 2021, owner=None)),
        )

        recorder.write_csvs(tmp_path)

        (row,) = read_rows(tmp_path / LEDGER_FILE)
        assert json.loads(row["credits_consumed"]) == [["E_a", 2019, "Yangtze", 2.0], [None, 2021, None, 1.0]]

    def test_expired_credits_record_one_row_each(self, tmp_path):
        """Per credit, not per sweep: the groupings and the reconciliation subtraction
        both fall out of the rows."""
        recorder = CapacityPolicyRecorder()
        recorder.record_expired(
            2030,
            [credit(1.0, 2019, tag="Jing-Jin-Ji", owner="E_a"), credit(2.0, 2020, product="steel")],
        )

        recorder.write_csvs(tmp_path)

        rows = read_rows(tmp_path / LEDGER_FILE)
        assert [row["operation"] for row in rows] == ["expired", "expired"]
        assert [(row["year"], row["vintage_year"]) for row in rows] == [("2030", "2019"), ("2030", "2020")]
        assert [(row["region_tag"], row["owner_id"], row["product"]) for row in rows] == [
            ("Jing-Jin-Ji", "E_a", "iron"),
            ("", "", "steel"),
        ]

    def test_an_empty_sweep_records_nothing(self, tmp_path):
        recorder = CapacityPolicyRecorder()
        recorder.record_expired(2030, [])

        recorder.write_csvs(tmp_path)

        assert read_rows(tmp_path / LEDGER_FILE) == []

    def test_blank_cells_stay_blank(self, tmp_path):
        """Not-applicable columns must not acquire a value of their own."""
        recorder = CapacityPolicyRecorder()
        recorder.record_ledger(year=2026, operation="blocked_expansion", amount_t=1.0, blocked_reason="short")

        recorder.write_csvs(tmp_path)

        (row,) = read_rows(tmp_path / LEDGER_FILE)
        assert row["credits_consumed"] == ""
        assert row["vintage_year"] == ""
        assert row["region_tag"] == ""

    def test_state_aggregates_by_tag_owner_and_product(self, tmp_path):
        recorder = CapacityPolicyRecorder()
        recorder.record_state(
            2026,
            [
                credit(1.0, 2019, tag="Jing-Jin-Ji", owner="E_a"),
                credit(2.0, 2021, tag="Jing-Jin-Ji", owner="E_a"),
                credit(4.0, 2020, owner=None, product="steel"),
            ],
        )

        recorder.write_csvs(tmp_path)

        rows = read_rows(tmp_path / STATE_FILE)
        assert rows == [
            {
                "year": "2026",
                "region_tag": "",
                "owner_id": "",
                "product": "steel",
                "remaining_t": "4.0",
                "oldest_vintage": "2020",
            },
            {
                "year": "2026",
                "region_tag": "Jing-Jin-Ji",
                "owner_id": "E_a",
                "product": "iron",
                "remaining_t": "3.0",
                "oldest_vintage": "2019",
            },
        ]

    def test_gate_decision_columns_carry_the_fallback_flags(self, tmp_path):
        recorder = CapacityPolicyRecorder()
        recorder.record_gate_decision(
            year=2026,
            furnace_group_id="fg-1",
            geo_key="CHN:CN-HE",
            product="iron",
            old_technology="BF",
            old_reductant="coke_pci",
            new_technology="DRI",
            new_reductant=None,
            decision="ratio",
            capacity_t=3.0,
            ratio=1.5,
            permitted_t=2.0,
            new_used_conservative_fallback=True,
        )

        recorder.write_csvs(tmp_path)

        (row,) = read_rows(tmp_path / GATE_DECISIONS_FILE)
        assert row["decision"] == "ratio"
        assert row["product"] == "iron"
        assert row["old_used_conservative_fallback"] == "False"
        assert row["new_used_conservative_fallback"] == "True"


def flows_by_key(ledger: list[dict[str, str]], year: int) -> dict[tuple[str, str, str], float]:
    """Seed + deposits − consumed − expired + refunded, up to and including ``year``.

    Blocked rows are refusals and the discard row annotates a withdrawal already
    accounted for, so neither enters the sum; a withdrawal moves exactly the
    credit portions it consumed, which carry their own owner and tag, and a
    refund hands those same portions back under the same key.
    """
    totals: dict[tuple[str, str, str], float] = {}
    for row in ledger:
        if int(row["year"]) > year:
            continue
        if row["operation"] == "seed" or row["operation"].startswith("deposit_") or row["operation"] == "refunded":
            key = (row["region_tag"], row["owner_id"], row["product"])
            totals[key] = totals.get(key, 0.0) + float(row["amount_t"])
        elif row["operation"].startswith("expired"):
            key = (row["region_tag"], row["owner_id"], row["product"])
            totals[key] = totals.get(key, 0.0) - float(row["amount_t"])
        elif row["operation"].startswith("withdraw_"):
            for owner_id, _vintage, region_tag, amount in json.loads(row["credits_consumed"]):
                key = (region_tag or "", owner_id or "", row["product"])
                totals[key] = totals.get(key, 0.0) - amount
    return {key: total for key, total in totals.items() if total != 0.0}


def state_by_key(state: list[dict[str, str]], year: int) -> dict[tuple[str, str, str], float]:
    return {
        (row["region_tag"], row["owner_id"], row["product"]): float(row["remaining_t"])
        for row in state
        if int(row["year"]) == year
    }


class TestReconciliation:
    """The two views of one arithmetic, checked from the written files alone."""

    @pytest.fixture
    def written(self, tmp_path) -> Path:
        """A run's worth of pool traffic: seeds, deposits, grants, blocks, a discarded
        greenfield refunded at its original vintages, and a boundary purge."""
        pool = CapacityPool(inter_company_swap_cutoff_year=None, credit_validity_years=8)
        recorder = CapacityPolicyRecorder()
        seeds = [
            credit(6.0, 2020, tag="Jing-Jin-Ji", owner="E_a"),
            credit(4.0, 2021, owner=None),
            credit(2.0, 2022, owner="E_b", product="steel"),
        ]
        for seed in seeds:
            pool.deposit(seed)
            recorder.record_ledger(
                year=seed.vintage_year,
                operation="seed",
                amount_t=seed.amount_mt,
                region_tag=seed.region_tag,
                owner_id=seed.owner_id,
                product=seed.product,
                vintage_year=seed.vintage_year,
            )
        recorder.record_state(2025, pool.snapshot())

        deposit = credit(3.0, 2026, owner="E_b")
        pool.deposit(deposit)
        recorder.record_ledger(
            year=2026,
            operation="deposit_close",
            amount_t=deposit.amount_mt,
            region_tag=deposit.region_tag,
            owner_id=deposit.owner_id,
            product=deposit.product,
            vintage_year=deposit.vintage_year,
        )
        # Crosses two holders and lands mid-credit, so the queue keeps a remainder
        granted = pool.try_withdraw(7.0, None, product="iron", owner_id="E_c", year=2026)
        assert granted.granted
        assert len(granted.credits_consumed) == 2
        recorder.record_ledger(
            year=2026,
            operation="withdraw_expansion",
            amount_t=7.0,
            owner_id="E_c",
            product="iron",
            credits_consumed=granted.credits_consumed,
        )
        blocked = pool.try_withdraw(99.0, None, product="steel", owner_id="E_c", year=2026)
        assert not blocked.granted
        recorder.record_ledger(
            year=2026,
            operation="blocked_expansion",
            amount_t=99.0,
            owner_id="E_c",
            product="steel",
            blocked_reason=blocked.blocked_reason,
        )
        recorder.record_state(2026, pool.snapshot())

        # An end-of-life retirement: nobody decided it, so it carries its own operation
        retired = credit(1.5, 2027, tag="Jing-Jin-Ji", owner="E_a")
        pool.deposit(retired)
        recorder.record_ledger(
            year=2027,
            operation="deposit_close_end_of_life",
            amount_t=retired.amount_mt,
            region_tag=retired.region_tag,
            owner_id=retired.owner_id,
            product=retired.product,
            vintage_year=retired.vintage_year,
        )
        leaked = pool.try_withdraw(2.0, None, product="iron", owner_id="indi_CHN", year=2027, single_owner=True)
        assert leaked.granted
        recorder.record_ledger(
            year=2027,
            operation="withdraw_greenfield",
            amount_t=2.0,
            owner_id="indi_CHN",
            product="iron",
            credits_consumed=leaked.credits_consumed,
            attributed_owner_id=leaked.attributed_owner_id,
        )
        recorder.record_state(2027, pool.snapshot())

        # The greenfield is discarded: the slices go back at their original vintages,
        # and the discard row records the fact without re-entering the sum
        for consumed in leaked.credits_consumed:
            pool.refund(consumed)
            recorder.record_ledger(
                year=2028,
                operation="refunded",
                amount_t=consumed.amount_mt,
                region_tag=consumed.region_tag,
                owner_id=consumed.owner_id,
                product=consumed.product,
                vintage_year=consumed.vintage_year,
            )
        recorder.record_ledger(
            year=2028,
            operation="greenfield_discard",
            amount_t=2.0,
            owner_id="indi_CHN",
            product="iron",
            attributed_owner_id=leaked.attributed_owner_id,
        )
        recorder.record_state(2028, pool.snapshot())

        # The boundary purge catches the 2021 vintages, the refunded slice among them
        recorder.record_expired(2029, pool.purge_expired(2029))
        recorder.record_state(2029, pool.snapshot())

        recorder.write_csvs(tmp_path)
        return tmp_path

    @pytest.mark.parametrize("year", [2025, 2026, 2027, 2028, 2029])
    def test_state_equals_the_flows_stamped_up_to_that_year(self, written, year):
        ledger = read_rows(written / LEDGER_FILE)
        state = read_rows(written / STATE_FILE)

        assert state_by_key(state, year) == pytest.approx(flows_by_key(ledger, year))

    @pytest.mark.parametrize("year", [2025, 2026, 2027, 2028, 2029])
    def test_totals_reconcile_in_aggregate(self, written, year):
        ledger = read_rows(written / LEDGER_FILE)
        state = read_rows(written / STATE_FILE)

        assert sum(state_by_key(state, year).values()) == pytest.approx(sum(flows_by_key(ledger, year).values()))

    def test_refusals_and_leaks_would_break_a_naive_sum(self, written):
        """The excluded rows are not incidental: counting them changes the answer."""
        ledger = read_rows(written / LEDGER_FILE)
        excluded = sum(
            float(row["amount_t"])
            for row in ledger
            if row["operation"].startswith("blocked_") or row["operation"] == "greenfield_discard"
        )

        assert excluded > 0

    def test_expired_rows_are_readable_per_region_tag(self, written):
        """Expired-unused capacity is readable by key region and nationally, both
        groupings of the same rows."""
        ledger = read_rows(written / LEDGER_FILE)
        expired = [row for row in ledger if row["operation"] == "expired"]

        assert {row["region_tag"] for row in expired} == {""}
        assert {row["vintage_year"] for row in expired} == {"2021"}
        assert sum(float(row["amount_t"]) for row in expired) == pytest.approx(3.0)

    def test_the_refunded_slice_carries_its_original_vintage_into_the_purge(self, written):
        """It came back at vintage 2021, so it expired with the rest of that vintage
        rather than restarting its clock at the refund."""
        ledger = read_rows(written / LEDGER_FILE)
        (refunded,) = [row for row in ledger if row["operation"] == "refunded"]

        assert refunded["year"] == "2028"
        assert refunded["vintage_year"] == "2021"
        assert state_by_key(read_rows(written / STATE_FILE), 2029) == {
            ("Jing-Jin-Ji", "E_a", "iron"): pytest.approx(1.5),
            ("", "E_b", "iron"): pytest.approx(3.0),
            ("", "E_b", "steel"): pytest.approx(2.0),
        }

    def test_a_partial_consumption_keeps_its_vintage(self, written):
        """The remainder of a split credit stays where it was in the queue."""
        state = read_rows(written / STATE_FILE)
        remainder = [row for row in state if int(row["year"]) == 2026 and row["owner_id"] == ""]

        assert remainder == [
            {
                "year": "2026",
                "region_tag": "",
                "owner_id": "",
                "product": "iron",
                "remaining_t": "3.0",
                "oldest_vintage": "2021",
            }
        ]


class TestFinalYearRefresh:
    def test_refresh_overwrites_the_last_recorded_year(self):
        """The final boundary deposits after its own snapshot; the flush catches up."""
        recorder = CapacityPolicyRecorder()
        recorder.record_state(2029, [credit(1.0, 2029)])
        recorder.record_state(2030, [credit(1.0, 2029)])

        recorder.refresh_final_state([credit(1.0, 2029), credit(4.0, 2030)])

        assert [row["remaining_t"] for row in recorder._state_rows()] == [1.0, 5.0]

    def test_refresh_without_a_snapshot_has_no_year_to_use(self):
        recorder = CapacityPolicyRecorder()

        recorder.refresh_final_state([credit(1.0, 2030)])

        assert recorder._state_rows() == []


class TestEmissionOnlyWhenBound:
    def test_flush_writes_nothing_while_unbound(self, tmp_path):
        cp_handlers.flush_capacity_policy_outputs(tmp_path)

        assert list(tmp_path.iterdir()) == []

    def test_a_disabled_bootstrap_leaves_no_policy_files(self, tmp_path):
        """Through the bootstrap seam: disabled unbinds, so the flush emits nothing."""
        configure_capacity_policy(CapacityPolicyConfig(enabled=False), None, start_year=2025)

        cp_handlers.flush_capacity_policy_outputs(tmp_path)

        assert list(tmp_path.iterdir()) == []

    def test_a_bound_flush_writes_all_four(self, tmp_path, caplog):
        recorder = CapacityPolicyRecorder()
        pool = CapacityPool()
        pool.deposit(credit(2.0, 2026, owner="E_a"))
        recorder.record_state(2026, pool.snapshot())
        cp_handlers.bind_capacity_policy(evaluator=None, pool=pool, recorder=recorder)  # type: ignore[arg-type]

        with caplog.at_level("INFO", logger="steelo.capacity_policy.handlers"):
            cp_handlers.flush_capacity_policy_outputs(tmp_path)

        assert sorted(path.name for path in tmp_path.iterdir()) == sorted(POLICY_FILES)
        assert f"{STATE_FILE}=1" in caplog.text
