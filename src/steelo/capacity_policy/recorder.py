"""Run artefacts for the capacity policy: four CSVs written when the policy is bound.

The policy's effect is largely *what it prevented*, which no simulation output
records — reconstructing the pool arithmetic meant grepping logs. This recorder
collects the same facts the ``[CAPACITY POOL]`` INFO lines carry, at the same
call sites, and writes them at the end of a run.

Pure stdlib and free of domain imports: it holds row dicts and knows the CSV
schemas, nothing else. Emission is conditional on the binding, not on a config
switch — a disabled run never constructs a recorder, so it writes no files at
all rather than empty ones.

**Reconciliation invariant.** The ledger and the state snapshots are two views
of one arithmetic, and they agree exactly:

    state(Y) = seed + Σ deposits − Σ consumed − Σ expired + Σ refunded, all ≤ Y

per ``(region_tag, owner_id, product)`` and in aggregate, where "deposits" means
every ``deposit_*`` operation — the mechanism is part of the fact, not a
separate stock. It holds because the
yearly snapshot is taken *before* ``finalise_iteration`` increments the year, so
the transactions that boundary triggers (scheduled switches, end-of-life
closures) stamp Y+1 and land in the next snapshot; the boundary purge runs
after the increment and stamps Y+1 for the same reason; the final boundary
increments by zero, which is why the flush re-snapshots the pool under the last
recorded year label. ``blocked_*`` rows record refusals and ``greenfield_discard``
annotates a withdrawal already accounted for — as a debit, and again as
``refunded`` rows where the discard handed the slices back — so both stay
outside the sum.

All amounts are model tonnes, suffixed ``_t``: the pool's ``amount_mt`` naming
is an internal convention from the sheet side and must not leak into an
artefact. Blank cells mean not-applicable for that operation, except
``region_tag`` (untagged) and ``owner_id``/``attributed_owner_id`` (the unowned
pot), where blank is a real value.
"""

import csv
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

from .pool import Credit

logger = logging.getLogger(__name__)

LEDGER_FILE = "capacity_pool_ledger.csv"
STATE_FILE = "capacity_pool_state.csv"
MOTIONS_FILE = "china_pam_motions.csv"
GATE_DECISIONS_FILE = "capacity_pool_gate_decisions.csv"

LEDGER_COLUMNS = (
    "year",
    "operation",
    "amount_t",
    "region_tag",
    "owner_id",
    "product",
    "vintage_year",
    "credits_consumed",
    "blocked_reason",
    "attributed_owner_id",
    "geo_key",
    "furnace_group_id",
)

STATE_COLUMNS = ("year", "region_tag", "owner_id", "product", "remaining_t", "oldest_vintage")

MOTIONS_COLUMNS = (
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
)

GATE_DECISIONS_COLUMNS = (
    "year",
    "furnace_group_id",
    "geo_key",
    "old_technology",
    "old_reductant",
    "new_technology",
    "new_reductant",
    "decision",
    "ratio",
    "capacity_t",
    "permitted_t",
    "old_used_conservative_fallback",
    "new_used_conservative_fallback",
)

LEDGER_OPERATIONS = (
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

MOTION_KINDS = ("close", "renovate", "switch", "expansion", "greenfield", "pipeline")

MOTION_SOURCES = ("pam", "input_data")


class CapacityPolicyRecorder:
    """Collect the policy's run facts in memory and write them as four CSVs.

    One recorder lives on the binding for the length of a run. Rows are
    appended beside the log lines that already state the same facts, so the
    files and the log can be cross-checked line for line.
    """

    def __init__(self) -> None:
        self._ledger: list[dict[str, Any]] = []
        self._motions: list[dict[str, Any]] = []
        self._gate_decisions: list[dict[str, Any]] = []
        self._state: dict[int, tuple[Credit, ...]] = {}

    def record_ledger(
        self,
        *,
        year: int,
        operation: str,
        amount_t: float,
        region_tag: str | None = None,
        owner_id: str | None = None,
        product: str | None = None,
        vintage_year: int | None = None,
        credits_consumed: Sequence[Credit] = (),
        blocked_reason: str | None = None,
        attributed_owner_id: str | None = None,
        geo_key: str | None = None,
        furnace_group_id: str | None = None,
    ) -> None:
        """Append one pool transaction — including the ones that did not happen.

        Args:
            year: Year the transaction (or refusal) happened in; on ``seed``
                rows the vintage itself, which keeps every seed inside the
                reconciliation window.
            operation: One of :data:`LEDGER_OPERATIONS`.
            amount_t: Deposited, withdrawn, expired, refunded, refused or
                discarded capacity.
            region_tag: Cluster tag of the credit or of the request.
            owner_id: Depositor on deposits and seeds, holder on expired and
                refunded credits, withdrawer on expansions, ``indi_<iso3>`` on
                greenfield attempts.
            product: ``"iron"`` or ``"steel"``.
            vintage_year: Credit vintage on deposit, seed, expired and refunded
                rows — a refund keeps the vintage it was withdrawn under.
            credits_consumed: The consumed portions of a granted withdrawal;
                serialised as ``[owner_id, vintage_year, region_tag, amount_t]``
                tuples. The tag belongs in the tuple — a build may spend a
                tagged credit, so the request's own tag cannot reconstruct
                per-tag flows — which also makes the ledger a complete event
                log the pool's credit-level state replays from.
            blocked_reason: The pool's reason on ``blocked_*`` rows.
            attributed_owner_id: Funding holder of a granted greenfield; blank
                is the unowned pot.
            geo_key: Combined geo key of the plant or build location.
            furnace_group_id: The group the transaction belongs to, where the
                call site knows it — the withdrawal gates do not.

        Raises:
            ValueError: On an unknown ``operation``.
        """
        if operation not in LEDGER_OPERATIONS:
            raise ValueError(f"Unknown ledger operation {operation!r}; expected one of {LEDGER_OPERATIONS}")
        self._ledger.append(
            {
                "year": year,
                "operation": operation,
                "amount_t": amount_t,
                "region_tag": region_tag,
                "owner_id": owner_id,
                "product": product,
                "vintage_year": vintage_year,
                "credits_consumed": _dump_credits(credits_consumed),
                "blocked_reason": blocked_reason,
                "attributed_owner_id": attributed_owner_id,
                "geo_key": geo_key,
                "furnace_group_id": furnace_group_id,
            }
        )

    def record_expired(self, year: int, credits: Sequence[Credit]) -> None:
        """Append one ``expired`` row per credit the boundary purge removed.

        One row per credit rather than an aggregate: expired-unused capacity has
        to be readable per key region and nationally (Decision 27), and the
        per-credit rows are what make both groupings — and the reconciliation
        subtraction — fall out of the same file.
        """
        for credit in credits:
            self.record_ledger(
                year=year,
                operation="expired",
                amount_t=credit.amount_mt,
                region_tag=credit.region_tag,
                owner_id=credit.owner_id,
                product=credit.product,
                vintage_year=credit.vintage_year,
            )

    def record_motion(
        self,
        *,
        year: int,
        kind: str,
        source: str,
        plant_id: str,
        furnace_group_id: str,
        geo_key: str,
        old_technology: str | None = None,
        new_technology: str | None = None,
        old_capacity_t: float | None = None,
        new_capacity_t: float | None = None,
        owner_id: str | None = None,
        product: str | None = None,
        reductant: str | None = None,
    ) -> None:
        """Append one Chinese furnace-group lifecycle event.

        Motions are recorded whether or not the pool moved: an unshrunk switch
        deposits nothing but is still a motion, and the fleet's technology mix
        is only readable from the complete set.

        Args:
            year: Event year. Expansion rows stamp the decision year, since the
                command executes in-year; greenfield rows stamp construction
                start, one announced→construction transition later; pipeline
                rows stamp the year the group starts operating, the only point
                the run observes for capacity the input data already had in
                flight.
            kind: One of :data:`MOTION_KINDS`.
            source: One of :data:`MOTION_SOURCES` — what set the motion in
                motion: a model decision (``"pam"``), or the input dataset's
                own schedule (``"input_data"``: pipeline arrivals, and age-outs
                of groups the data delivered with their retirement already on
                the clock).
            plant_id: Plant the group sits on.
            furnace_group_id: The group itself.
            geo_key: Combined geo key of the plant.
            old_technology: Technology before the motion; on ``close`` rows the
                technology being closed.
            new_technology: Technology after it, on the build and switch kinds.
            old_capacity_t: Capacity before the motion; on ``close`` rows the
                capacity being closed.
            new_capacity_t: Capacity after it, on the build and switch kinds.
            owner_id: Owning plant group, by membership.
            product: ``"iron"`` or ``"steel"``.
            reductant: The group's chosen reductant at the moment of the event
                — post-switch on a switch, which is what makes drift against
                the gate decisions measurable.

        Raises:
            ValueError: On an unknown ``kind`` or ``source``.
        """
        if kind not in MOTION_KINDS:
            raise ValueError(f"Unknown motion kind {kind!r}; expected one of {MOTION_KINDS}")
        if source not in MOTION_SOURCES:
            raise ValueError(f"Unknown motion source {source!r}; expected one of {MOTION_SOURCES}")
        self._motions.append(
            {
                "year": year,
                "kind": kind,
                "source": source,
                "plant_id": plant_id,
                "furnace_group_id": furnace_group_id,
                "geo_key": geo_key,
                "old_technology": old_technology,
                "new_technology": new_technology,
                "old_capacity_t": old_capacity_t,
                "new_capacity_t": new_capacity_t,
                "owner_id": owner_id,
                "product": product,
                "reductant": reductant,
            }
        )

    def has_renovation(self, furnace_group_id: str) -> bool:
        """True when an in-run renovation motion was recorded for this group.

        The end-of-life source rule needs it: a renovation resets the lifetime
        clock without stamping ``created_by_PAM``, so a later age-out runs out
        a model-set schedule even on a data-born group.
        """
        return any(row["kind"] == "renovate" and row["furnace_group_id"] == furnace_group_id for row in self._motions)

    def record_gate_decision(
        self,
        *,
        year: int,
        furnace_group_id: str | None,
        geo_key: str,
        old_technology: str,
        old_reductant: str | None,
        new_technology: str,
        new_reductant: str | None,
        decision: str,
        capacity_t: float,
        ratio: float | None = None,
        permitted_t: float | None = None,
        old_used_conservative_fallback: bool = False,
        new_used_conservative_fallback: bool = False,
    ) -> None:
        """Append one ② REPLACE evaluation — the reductant-drift evidence base.

        Args:
            year: Decision year.
            furnace_group_id: The group being evaluated, threaded from the
                decision path; None on evaluations made outside it.
            geo_key: Combined geo key of the plant.
            old_technology: Technology being replaced.
            old_reductant: Its reductant, as the decision path knows it.
            new_technology: Candidate technology.
            new_reductant: The reductant the candidate is evaluated with.
            decision: ``"ratio"`` or ``"blocked_utilization"``.
            capacity_t: The group's capacity going into the evaluation.
            ratio: Resolved swap ratio; blank when the utilisation gate blocked.
            permitted_t: Capacity the candidate may build; blank when blocked.
            old_used_conservative_fallback: Whether the old route was
                classified by the worst-case synthesis over its reductant rows
                rather than by an authored row.
            new_used_conservative_fallback: The same for the candidate route.
        """
        self._gate_decisions.append(
            {
                "year": year,
                "furnace_group_id": furnace_group_id,
                "geo_key": geo_key,
                "old_technology": old_technology,
                "old_reductant": old_reductant,
                "new_technology": new_technology,
                "new_reductant": new_reductant,
                "decision": decision,
                "ratio": ratio,
                "capacity_t": capacity_t,
                "permitted_t": permitted_t,
                "old_used_conservative_fallback": old_used_conservative_fallback,
                "new_used_conservative_fallback": new_used_conservative_fallback,
            }
        )

    def record_state(self, year: int, credits: Iterable[Credit]) -> None:
        """Store the pool's credits under ``year``, replacing any earlier snapshot for it."""
        self._state[year] = tuple(credits)

    def refresh_final_state(self, credits: Iterable[Credit]) -> None:
        """Re-snapshot the last recorded year at flush time.

        The final boundary increments the year by zero, so its end-of-life
        closures deposit *after* that year's snapshot was taken. Refreshing
        keeps the reconciliation invariant true for the last year too. A run
        that finished no year has no label to file the pool under and is left
        alone.
        """
        if not self._state:
            return
        self.record_state(max(self._state), credits)

    def write_csvs(self, output_dir: Path) -> dict[str, int]:
        """Write all four CSVs, headers included even where a table has no rows.

        Args:
            output_dir: Run root; created if it does not exist.

        Returns:
            Data-row count per file name, for the flush log line.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        for name, columns, rows in (
            (LEDGER_FILE, LEDGER_COLUMNS, self._ledger),
            (STATE_FILE, STATE_COLUMNS, self._state_rows()),
            (MOTIONS_FILE, MOTIONS_COLUMNS, self._motions),
            (GATE_DECISIONS_FILE, GATE_DECISIONS_COLUMNS, self._gate_decisions),
        ):
            with (output_dir / name).open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
            counts[name] = len(rows)
        return counts

    def _state_rows(self) -> list[dict[str, Any]]:
        """Aggregate each year's credits to one row per (tag, owner, product).

        Carries the pool's own caveat: under ``banked_credit_rule="expire"``
        the remaining capacity overstates what is usable, because that rule is
        an applicability predicate at withdrawal, not a purge of the queue. The
        shelf life (``credit_validity_years``) is the other concept and does
        sweep, so it leaves these rows honest.
        """
        rows: list[dict[str, Any]] = []
        for year in sorted(self._state):
            totals: dict[tuple[str | None, str | None, str], float] = {}
            oldest: dict[tuple[str | None, str | None, str], int] = {}
            for credit in self._state[year]:
                key = (credit.region_tag, credit.owner_id, credit.product)
                totals[key] = totals.get(key, 0.0) + credit.amount_mt
                oldest[key] = min(oldest.get(key, credit.vintage_year), credit.vintage_year)
            for key in sorted(totals, key=lambda k: (k[0] or "", k[1] or "", k[2])):
                region_tag, owner_id, product = key
                rows.append(
                    {
                        "year": year,
                        "region_tag": region_tag,
                        "owner_id": owner_id,
                        "product": product,
                        "remaining_t": totals[key],
                        "oldest_vintage": oldest[key],
                    }
                )
        return rows


def _dump_credits(credits: Sequence[Credit]) -> str | None:
    """Serialise consumed credit portions compactly, or None when there are none."""
    if not credits:
        return None
    return json.dumps(
        [[credit.owner_id, credit.vintage_year, credit.region_tag, credit.amount_mt] for credit in credits],
        separators=(",", ":"),
    )
