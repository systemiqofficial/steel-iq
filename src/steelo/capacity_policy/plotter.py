"""Capacity-pool charts in the Steel-IQ house style, drawn at the end of a run.

The chart set is the one kept from the capacity-pool experiments: the drawable
pool by build location, the pool by region tag as a stacked area, the capacity
the policy refused and the pool's annual deposits and withdrawals — each drawn
per product, because iron and steel are separate stocks — plus the technology
mix of what was built, split by the motion that built it.

Numbers come from the four CSVs :mod:`steelo.capacity_policy.recorder` flushes
to ``<output>/data/policy``. A policy-OFF run writes no artefacts, so
:meth:`CapacityPoolPlotter.plot_all` draws nothing. An unrecognised ledger
operation or gate decision raises rather than being dropped from a sum silently.

Region tags, refusal reasons, flow labels and motion kinds have no house colour
mapping — they are capacity-policy concepts — so they take a stable slice of the
house region palette in first-seen order, which keeps the assignment
reproducible across runs.
"""

import csv
import logging
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch

from steelo.utilities.steeliq_plotter import SteelPlotter

from .recorder import (
    GATE_DECISIONS_FILE,
    LEDGER_FILE,
    LEDGER_OPERATIONS,
    MOTIONS_FILE,
    STATE_FILE,
)

logger = logging.getLogger(__name__)

MT = 1e6

DEPOSITS = {
    "deposit_close": "Deposit — closure",
    "deposit_close_end_of_life": "Deposit — end-of-life retirement",
    "deposit_replace": "Deposit — penalised replacement",
}
WITHDRAWALS = {
    "withdraw_expansion": "Withdrawal — expansion",
    "withdraw_greenfield": "Withdrawal — greenfield",
}
BLOCKED = ("blocked_expansion", "blocked_greenfield")
BLOCKED_REASONS = {
    "insufficient_applicable_pool": "Pool short of applicable credit",
    "no_single_owner_with_sufficient_credits": "No single holder covers a greenfield",
}
GATE_DECISIONS = {"ratio", "blocked_utilization"}
BUILD_KINDS = ("switch", "expansion", "greenfield", "pipeline")

# Fixed-order slice of the house region palette for keys the house has no colour for
HOUSE_CATEGORICAL_REGIONS = (
    "Europe",
    "Developed Asia",
    "India",
    "Other Asia",
    "Subsaharan Africa",
    "Latin America",
    "Oceania",
)


@dataclass(frozen=True)
class PolicyArtefacts:
    """One run's four policy CSVs, plus the years the pool was tracked over."""

    ledger: list[dict[str, str]]
    state: list[dict[str, str]]
    motions: list[dict[str, str]]
    gates: list[dict[str, str]]
    years: list[int]

    @property
    def products(self) -> list[str]:
        """Products with a pool state, sorted."""
        return sorted({row["product"] for row in self.state})

    @property
    def clusters(self) -> list[str]:
        """Key-region cluster tags seen in the pool state, sorted."""
        return sorted({row["region_tag"] for row in self.state if row["region_tag"]})


def load_artefacts(policy_dir: Optional[Path]) -> Optional[PolicyArtefacts]:
    """Read a run's policy artefacts, rejecting any vocabulary this module cannot sum.

    Args:
        policy_dir: ``SimulationConfig.policy_output_dir`` — where the recorder
            flushed the four CSVs. None, or a directory without them, means the
            policy was off for the run.

    Returns:
        The four tables plus the state file's year range — the canonical axis for
        every chart (the ledger's own range starts at the seed vintages) — or
        None when there is nothing to plot.

    Raises:
        ValueError: On a ledger operation or gate decision added to the code
            since this module was written.
    """
    if policy_dir is None:
        return None
    paths = [policy_dir / name for name in (LEDGER_FILE, STATE_FILE, MOTIONS_FILE, GATE_DECISIONS_FILE)]
    if not all(path.exists() for path in paths):
        return None

    def read(path: Path) -> list[dict[str, str]]:
        with path.open() as handle:
            return list(csv.DictReader(handle))

    ledger, state, motions, gates = (read(path) for path in paths)
    unknown_operations = {row["operation"] for row in ledger} - set(LEDGER_OPERATIONS)
    if unknown_operations:
        raise ValueError(f"Unrecognised ledger operations {sorted(unknown_operations)} — teach this module about them")
    unknown_decisions = {row["decision"] for row in gates} - GATE_DECISIONS
    if unknown_decisions:
        raise ValueError(f"Unrecognised gate decisions {sorted(unknown_decisions)}")

    years = sorted({int(row["year"]) for row in state})
    if not years:
        return None
    return PolicyArtefacts(ledger, state, motions, gates, years)


def _per_year(
    years: list[int],
    rows: Iterable[dict[str, str]],
    key: Callable[[dict[str, str]], str],
    value: Callable[[dict[str, str]], float],
) -> dict[str, list[float]]:
    """Sum ``value`` per ``key`` per year, in Mt, dropping keys that stay at zero."""
    buckets: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        buckets[key(row)][int(row["year"])] += value(row) / MT
    return {
        name: [totals.get(year, 0.0) for year in years] for name, totals in buckets.items() if sum(totals.values()) > 0
    }


def _ordered(series: dict[str, list[float]], order: list[str]) -> dict[str, list[float]]:
    """Reorder ``series`` by ``order``, dropping names it does not contain."""
    return {name: series[name] for name in order if name in series}


def pool_by_tag(art: PolicyArtefacts) -> dict[str, dict[str, list[float]]]:
    """Credit in the pool per year, partitioned by the tag that restricts its use.

    Returns:
        Per product, a tag → per-year Mt series. Every credit carries exactly
        one tag or none, so the series sum to the pool and may be stacked.
    """
    frames = {}
    for product in art.products:
        rows = [row for row in art.state if row["product"] == product]
        series = _per_year(
            art.years, rows, lambda row: row["region_tag"] or "untagged", lambda row: float(row["remaining_t"])
        )
        frames[product] = _ordered(series, art.clusters + ["untagged"])
    return frames


def applicable_pool(art: PolicyArtefacts) -> dict[str, tuple[list[float], dict[str, list[float]]]]:
    """What each kind of build may draw on — nested entitlements, never stacked.

    Returns:
        Per product, the whole pool per year and each cluster's ring-fenced pot.
        A build inside cluster X may spend only X's pot; a build outside every
        key cluster carries no tag requirement and may spend the whole pool,
        cluster credit included. Summing the lines double-counts.
    """
    frames = {}
    for product, series in pool_by_tag(art).items():
        total = [sum(values[index] for values in series.values()) for index in range(len(art.years))]
        frames[product] = (total, {tag: values for tag, values in series.items() if tag != "untagged"})
    return frames


def policy_bite(art: PolicyArtefacts) -> dict[str, dict[str, list[float]]]:
    """Capacity the policy refused each year, by product and reason.

    Refused, not deferred elsewhere: a blocked expansion or greenfield retries
    in later years (up to the retry cap), while a utilisation block removes the
    candidate from that year's menu altogether.

    Returns:
        Per product, a reason → per-year Mt series.
    """
    refusals = [
        {
            "year": row["year"],
            "product": row["product"],
            "reason": BLOCKED_REASONS[row["blocked_reason"]],
            "amount": row["amount_t"],
        }
        for row in art.ledger
        if row["operation"] in BLOCKED
    ]
    refusals += [
        {
            "year": row["year"],
            "product": row["product"],
            "reason": "Utilisation gate (replacement refused)",
            "amount": row["capacity_t"],
        }
        for row in art.gates
        if row["decision"] == "blocked_utilization"
    ]
    return {
        product: _per_year(
            art.years,
            [row for row in refusals if row["product"] == product],
            lambda row: row["reason"],
            lambda row: float(row["amount"]),
        )
        for product in sorted({row["product"] for row in refusals})
    }


def annual_flows(art: PolicyArtefacts) -> dict[str, tuple[dict[str, list[float]], dict[str, list[float]]]]:
    """Deposits banked and withdrawals spent per year, both as positive Mt.

    Per product, because iron and steel are separate stocks. Seed credit is
    excluded: it is the opening balance, not a flow.

    Returns:
        Per product, the ``(deposits, withdrawals)`` label → per-year Mt series.
    """
    flows = [row for row in art.ledger if row["operation"] in DEPOSITS or row["operation"] in WITHDRAWALS]
    frames = {}
    for product in sorted({row["product"] for row in flows}):
        rows = [row for row in flows if row["product"] == product]
        deposits = _per_year(
            art.years,
            [row for row in rows if row["operation"] in DEPOSITS],
            lambda row: DEPOSITS[row["operation"]],
            lambda row: float(row["amount_t"]),
        )
        withdrawals = _per_year(
            art.years,
            [row for row in rows if row["operation"] in WITHDRAWALS],
            lambda row: WITHDRAWALS[row["operation"]],
            lambda row: float(row["amount_t"]),
        )
        frames[product] = (
            _ordered(deposits, list(DEPOSITS.values())),
            _ordered(withdrawals, list(WITHDRAWALS.values())),
        )
    return frames


def technology_mix_by_kind(art: PolicyArtefacts) -> dict[str, dict[str, float]]:
    """Capacity moved into each technology, split by the motion that moved it.

    Returns:
        Technology → {Switch/Expansion/Greenfield/Pipeline → Mt}, largest
        technology first. Only motions that point capacity at a technology
        count; closes and renovations do not.
    """
    by_technology: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in art.motions:
        if row["kind"] in BUILD_KINDS and row["new_technology"]:
            by_technology[row["new_technology"]][row["kind"].capitalize()] += float(row["new_capacity_t"] or 0) / MT
    ranked = sorted(by_technology.items(), key=lambda item: -sum(item[1].values()))
    return {technology: dict(kinds) for technology, kinds in ranked}


class CapacityPoolPlotter(SteelPlotter):
    """House-style charts of what the capacity policy held, refused and rebuilt.

    Inherits the footer, legend, save and CSV-export behaviour from
    :class:`SteelPlotter`; everything lands in ``plots_dir/capacity_pool``.

    Example:
        >>> plotter = CapacityPoolPlotter(config=PlotConfig(), plot_paths=env.plot_paths)
        >>> plotter.plot_all(config.policy_output_dir)
    """

    CAPACITY_POOL_SUBDIR = "capacity_pool"
    CONTEXT = "#B0B0B0"
    CONTEXT_INK = "#404040"

    def plot_all(self, policy_dir: Optional[Path]) -> list[Path]:
        """Draw the full capacity-pool chart set from a run's policy artefacts.

        Args:
            policy_dir: Where the recorder flushed its CSVs; None or an empty
                directory (a policy-OFF run) draws nothing.

        Returns:
            Paths of the saved figures (empty when the policy was off).
        """
        art = load_artefacts(policy_dir)
        if art is None:
            self.logger.info("No capacity-pool artefacts found — skipping capacity pool charts")
            return []
        saved = [
            *self.plot_applicable_pool(art),
            *self.plot_pool_area(art),
            *self.plot_policy_bite(art),
            *self.plot_annual_flows(art),
            self.plot_technology_mix(art),
        ]
        return [path for path in saved if path is not None]

    # ------------------------------------------------------------------ helpers

    def _categorical_colours(self, keys: Iterable[str]) -> list[Any]:
        """A technology keeps its house colour; anything else takes a stable palette slot."""
        palette = [self.config.region_colors[name] for name in HOUSE_CATEGORICAL_REGIONS]
        colours: list[Any] = []
        slot = 0
        for key in keys:
            if key in self.config.tech_colors:
                colours.append(self.config.tech_colors[key])
            else:
                colours.append(palette[slot % len(palette)])
                slot += 1
        return colours

    def _year_ticks(self, ax, years: list[int], step: int = 5) -> None:
        """Thin categorical bar labels to every ``step``th year."""
        ax.set_xticks([index for index, year in enumerate(years) if (year - years[0]) % step == 0])
        ax.set_xticklabels([str(year) for year in years if (year - years[0]) % step == 0], rotation=0)

    def _frame(self, years: list[int], series: dict[str, list[float]]) -> pd.DataFrame:
        return pd.DataFrame(series, index=pd.Index(years, name="year"))

    def _save(self, fig, table: pd.DataFrame, name: str) -> Path:
        filename = f"{self.CAPACITY_POOL_SUBDIR}/{name}"
        self._save_chart_data_to_csv(table, filename, subdir="plots_dir")
        return self._save_figure(fig, filename, subdir="plots_dir")

    # ------------------------------------------------------------------ charts

    def plot_applicable_pool(self, art: PolicyArtefacts) -> list[Path]:
        """nested entitlements: the whole pool as a band, each cluster's pot as a line."""
        saved = []
        for product, (total, clusters) in applicable_pool(art).items():
            fig, ax = plt.subplots(figsize=self.config.default_figsize_wide)
            ax.fill_between(art.years, total, color=self.CONTEXT, alpha=0.35)
            ax.plot(art.years, total, color=self.CONTEXT_INK, linewidth=2, label="Whole pool")
            for colour, (tag, values) in zip(self._categorical_colours(clusters), clusters.items()):
                ax.plot(art.years, values, color=colour, linewidth=2, label=tag)
            ax.set_title(
                f"{product.capitalize()} Available Credits in Capacity Pool (Key Regions and Total Pool)",
                fontsize=14,
                fontweight="bold",
            )
            ax.set_xlabel("Year", fontsize=12)
            ax.set_ylabel("Drawable Credit [Mt]", fontsize=12)
            ax.grid(axis="y", alpha=self.config.grid_alpha, linestyle=self.config.grid_linestyle)
            legend = self._style_legend(ax, title="Build location")
            self._ensure_y_axis_starts_at_zero(ax)
            fig.tight_layout()
            fig.canvas.draw()  # realise the legend box so the note can sit right under it
            legend_box = legend.get_window_extent().transformed(ax.transAxes.inverted())
            ax.text(
                legend_box.x0,
                legend_box.y0 - 0.03,
                textwrap.fill(
                    "Nested, not stacked: a build in a named cluster may spend only that cluster's credit; "
                    "a build outside every key cluster may spend the whole pool, cluster credit included.",
                    width=32,
                ),
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=9,
                color="dimgray",
                style="italic",
            )
            table = self._frame(art.years, clusters)
            table["whole pool"] = total
            saved.append(self._save(fig, table, f"{product}_pool_nested.png"))
        return saved

    def plot_pool_area(self, art: PolicyArtefacts) -> list[Path]:
        """the pool's credit per year as a stacked area, partitioned by region tag."""
        saved = []
        for product, series in pool_by_tag(art).items():
            if not series:
                continue
            table = self._frame(art.years, series)
            fig, ax = plt.subplots(figsize=self.config.default_figsize_wide)
            colours = self._categorical_colours(table.columns)
            ax.stackplot(table.index, *[table[column] for column in table.columns], colors=colours)
            ax.set_title(
                f"{product.capitalize()} Available Credits in Capacity Pool (Stacked Area)",
                fontsize=14,
                fontweight="bold",
            )
            ax.set_xlabel("Year", fontsize=12)
            ax.set_ylabel("Credit Available [Mt]", fontsize=12)
            ax.grid(axis="y", alpha=self.config.grid_alpha, linestyle=self.config.grid_linestyle)
            handles = [Patch(facecolor=colour, label=column) for colour, column in zip(colours, table.columns)]
            self._style_legend(ax, title="Region tag", handles=handles, labels=list(table.columns))
            self._ensure_y_axis_starts_at_zero(ax)
            fig.tight_layout()
            saved.append(self._save(fig, table, f"{product}_pool_area.png"))
        return saved

    def plot_policy_bite(self, art: PolicyArtefacts) -> list[Path]:
        """capacity refused by the policy each year, stacked by reason, per product."""
        saved = []
        for product, reasons in policy_bite(art).items():
            if not reasons:
                continue
            table = self._frame(art.years, reasons)
            fig, ax = plt.subplots(figsize=self.config.default_figsize_wide)
            colours = self._categorical_colours(table.columns)
            table.plot.bar(stacked=True, ax=ax, color=colours, width=0.85)
            ax.set_title(
                f"Annual Refused {product.capitalize()} Capacity by Capacity Policy",
                fontsize=14,
                fontweight="bold",
            )
            ax.set_xlabel("Year", fontsize=12)
            ax.set_ylabel("Capacity Refused [Mt]", fontsize=12)
            ax.grid(axis="y", alpha=self.config.grid_alpha, linestyle=self.config.grid_linestyle)
            self._year_ticks(ax, art.years)
            labels = [textwrap.fill(column, width=24) for column in table.columns]
            handles = [Patch(facecolor=colour, label=label) for colour, label in zip(colours, labels)]
            self._style_legend(ax, title="Reason", handles=handles, labels=labels)
            self._ensure_y_axis_starts_at_zero(ax)
            fig.tight_layout()
            saved.append(self._save(fig, table, f"{product}_capacity_refused.png"))
        if not saved:
            self.logger.info("The policy refused nothing in this run — skipping the policy bite charts")
        return saved

    def plot_annual_flows(self, art: PolicyArtefacts) -> list[Path]:
        """the pool's yearly capacity flow per product: deposits stacked above zero, withdrawals below."""
        saved = []
        for product, (deposits, withdrawals) in annual_flows(art).items():
            if not deposits and not withdrawals:
                continue
            series = dict(deposits) | {label: [-value for value in values] for label, values in withdrawals.items()}
            table = self._frame(art.years, series)
            fig, ax = plt.subplots(figsize=self.config.default_figsize_wide)
            colours = self._categorical_colours(table.columns)
            table.plot.bar(stacked=True, ax=ax, color=colours, width=0.85)
            ax.axhline(0, color="black", linewidth=1.0)
            ax.set_title(f"Annual {product.capitalize()} Capacity Pool Flows", fontsize=14, fontweight="bold")
            ax.set_xlabel("Year", fontsize=12)
            ax.set_ylabel("Capacity [Mt]", fontsize=12)
            ax.grid(axis="y", alpha=self.config.grid_alpha, linestyle=self.config.grid_linestyle)
            self._year_ticks(ax, art.years)
            labels = [textwrap.fill(column, width=24) for column in table.columns]
            handles = [Patch(facecolor=colour, label=label) for colour, label in zip(colours, labels)]
            self._style_legend(ax, title="Flow", handles=handles, labels=labels)
            fig.tight_layout()
            saved.append(self._save(fig, table, f"{product}_capacity_flows.png"))
        if not saved:
            self.logger.info("No deposits or withdrawals in this run — skipping the annual flow charts")
        return saved

    def plot_technology_mix(self, art: PolicyArtefacts) -> Optional[Path]:
        """capacity moved into each technology, stacked by the motion that moved it."""
        mix = technology_mix_by_kind(art)
        if not mix:
            self.logger.info("No switch, expansion or greenfield moved capacity — skipping the technology mix chart")
            return None
        kinds = [kind.capitalize() for kind in BUILD_KINDS]
        technologies = list(reversed(mix))  # barh draws bottom-up, so largest ends on top
        fig, ax = plt.subplots(figsize=(self.config.default_figsize_wide[0], 0.45 * len(technologies) + 3))
        palette = [self.config.region_colors[name] for name in HOUSE_CATEGORICAL_REGIONS]
        left = [0.0] * len(technologies)
        for kind, colour in zip(kinds, palette):
            values = [mix[technology].get(kind, 0.0) for technology in technologies]
            ax.barh(technologies, values, left=left, color=colour, height=0.7, label=kind)
            left = [total + value for total, value in zip(left, values)]
        for position, total in enumerate(left):
            ax.text(total, position, f" {total:,.0f} Mt", va="center", fontsize=9)
        ax.set_title("Capacity Moved into Each Technology", fontsize=14, fontweight="bold")
        ax.set_xlabel("Capacity [Mt]", fontsize=12)
        ax.grid(axis="x", alpha=self.config.grid_alpha, linestyle=self.config.grid_linestyle)
        handles = [Patch(facecolor=colour, label=kind) for kind, colour in zip(kinds, palette)]
        self._style_legend(ax, title="Motion", handles=handles, labels=kinds)
        fig.tight_layout()
        table = pd.DataFrame(mix).T.reindex(columns=kinds).fillna(0.0).rename_axis("technology")
        return self._save(fig, table, "technology_mix.png")
