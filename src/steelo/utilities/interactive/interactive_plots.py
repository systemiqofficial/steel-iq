"""Self-contained interactive plotly viewers written to ``<plots>/interactive/``.

Each viewer is one HTML file with plotly.js, the shared toolbar shell
(``common.js`` / ``common.css``) and the chart's data inlined, so it opens
anywhere without a server. :class:`InteractivePlotter` mirrors
:class:`~steelo.utilities.steeliq_plotter.SteelPlotter`: one instance per run,
one method per chart. A chart is a template (its own controls plus a
``render()``) and a packing module that turns a run output file into compact
rows; the shell gives every chart the same run selector and geography filter
(countries, sub-national geo units, trade blocs, regions) and an optional
technology filter, all driven by the country mappings passed in here.
"""

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from matplotlib.colors import to_hex
from plotly.offline import get_plotlyjs

from steelo.domain.models import CountryMapping
from steelo.utilities.plotting import region2colours, tech2colours

from . import capacity_production, cost_curves, emissions, trade_matrix

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent


def trade_bloc_members(country_mappings: list[CountryMapping]) -> dict[str, list[str]]:
    """Trade blocs and their member ISO3 codes, from the boolean membership attributes.

    Args:
        country_mappings: The run's country mappings (the Country mapping sheet).

    Returns:
        ``{bloc_name: [iso3, ...]}`` for every bloc with at least one member, both sorted.
        Any boolean attribute on the mapping counts as a bloc, so blocs added as new
        sheet columns appear without changes here.
    """
    members: dict[str, list[str]] = defaultdict(list)
    for mapping in country_mappings:
        for name, value in vars(mapping).items():
            if isinstance(value, bool) and value:
                members[name].append(mapping.iso3)
    return {bloc: sorted(iso3s) for bloc, iso3s in sorted(members.items())}


def geo_info(country_mappings: list[CountryMapping]) -> dict[str, dict[str, str]]:
    """Country name and output region per ISO3, for labels and the region filter/grouping.

    Args:
        country_mappings: The run's country mappings.

    Returns:
        ``{iso3: {"country": name, "region": region_for_outputs}}``.
    """
    return {m.iso3: {"country": m.country, "region": m.region_for_outputs} for m in country_mappings}


def geo_unit_names(geo_hierarchy_json: Optional[Path]) -> dict[str, str]:
    """Display names of the sub-national units, keyed by geo_key (``CHN:CN-HE`` → ``Hebei``).

    Args:
        geo_hierarchy_json: The prepared ``geo_hierarchy.json``, or None.

    Returns:
        ``{geo_key: display_name}``; empty when no file is given or it does not exist
        (older geo-data packages prepare no hierarchy), so the viewers fall back to codes.
    """
    if geo_hierarchy_json is None or not geo_hierarchy_json.is_file():
        return {}
    return {row["geo_key"]: row["display_name"] for row in json.loads(geo_hierarchy_json.read_text())}


def run_display_title(run_name: Optional[str], fallback: str, post_processed_csv: Path) -> str:
    """Run title shown in the viewers: the run's name with its completion time in brackets.

    Args:
        run_name: Human-readable run name (e.g. from ``--run-name``), or None.
        fallback: Name used when no run name was given (e.g. the output dir name ``sim_<ts>``).
        post_processed_csv: The run's ``post_processed_<date>_<time>.csv``, whose file name
            carries the completion timestamp.

    Returns:
        ``"<name> (<date> <hh:mm>)"``, or just the name when the CSV name holds no timestamp.
    """
    stamp = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})", post_processed_csv.name)
    suffix = f" ({stamp.group(1)} {stamp.group(2)}:{stamp.group(3)})" if stamp else ""
    return f"{run_name or fallback}{suffix}"


def _hex(colours: dict[str, str]) -> dict[str, str]:
    return {name: to_hex(colour) for name, colour in colours.items()}


class InteractivePlotter:
    """Writes the interactive viewers of one run.

    Example:
        >>> interactive = InteractivePlotter(plots_dir, country_mappings, run_title="sim_2026")
        >>> interactive.plot_emissions(post_processed_csv)
        >>> interactive.plot_capacity_and_production(post_processed_csv)
        >>> interactive.plot_cost_curves(post_processed_csv, market_prices_csv, clearing)
        >>> interactive.plot_trade_matrix(tm_dir)
    """

    SUBDIR = "interactive"

    def __init__(
        self,
        plots_dir: Path,
        country_mappings: list[CountryMapping],
        run_title: str,
        geo_hierarchy_json: Optional[Path] = None,
    ) -> None:
        """
        Args:
            plots_dir: The run's plots directory; viewers go to its ``interactive`` subfolder.
            country_mappings: The run's country mappings (trade blocs, regions, country names).
            run_title: Run name shown in chart titles (e.g. the output dir name).
            geo_hierarchy_json: The prepared ``geo_hierarchy.json`` for sub-national unit names;
                None (or a missing file) leaves the units labelled by code.
        """
        self.output_dir = plots_dir / self.SUBDIR
        self.country_mappings = country_mappings
        self.run_title = run_title
        self.geo_unit_names = geo_unit_names(geo_hierarchy_json)
        self._tables: dict[Path, pd.DataFrame] = {}

    def _read_post_processed(self, post_processed_csv: Path, viewer: str) -> Optional[pd.DataFrame]:
        """The post-processed table, read once per plotter; None (with a warning) when the file is missing."""
        if post_processed_csv not in self._tables:
            if not post_processed_csv.is_file():
                logger.warning("No post-processed table at %s — skipping the %s viewer", post_processed_csv, viewer)
                return None
            self._tables[post_processed_csv] = pd.read_csv(post_processed_csv, low_memory=False)
        return self._tables[post_processed_csv]

    def plot_emissions(self, post_processed_csv: Path) -> Optional[Path]:
        """Write the emissions viewer (``emissions.html``) from the post-processed furnace-group table.

        Args:
            post_processed_csv: The run's ``post_processed_<timestamp>.csv``.

        Returns:
            The written path, or None when the table is missing or has no emissions columns
            (logged as warnings so the plot stage never fails).
        """
        table = self._read_post_processed(post_processed_csv, "emissions")
        if table is None:
            return None
        try:
            emission_keys, aggregated = emissions.aggregate_emissions(table)
        except ValueError as exc:
            logger.warning("%s — skipping the emissions viewer", exc)
            return None
        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": f"Furnace-group emissions from {post_processed_csv.name}.",
                "rows": emissions.pack_rows(aggregated, emission_keys),
            },
        }
        path = self._write("emissions.html", self._config("Emissions", emissionKeys=emission_keys), data)
        logger.info("Wrote emissions viewer %s (%d aggregated rows)", path, len(aggregated))
        return path

    def plot_capacity_and_production(self, post_processed_csv: Path) -> Optional[Path]:
        """Write the capacity and production viewer (``capacity_and_production.html``).

        Args:
            post_processed_csv: The run's ``post_processed_<timestamp>.csv``.

        Returns:
            The written path, or None when the table is missing or lacks the capacity or
            production column (logged as warnings so the plot stage never fails).
        """
        table = self._read_post_processed(post_processed_csv, "capacity and production")
        if table is None:
            return None
        try:
            aggregated = capacity_production.aggregate_capacity_production(table)
        except ValueError as exc:
            logger.warning("%s — skipping the capacity and production viewer", exc)
            return None
        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": f"Furnace-group capacity and production from {post_processed_csv.name}.",
                "rows": capacity_production.pack_rows(aggregated),
            },
        }
        path = self._write("capacity_and_production.html", self._config("Capacity and production"), data)
        logger.info("Wrote capacity and production viewer %s (%d aggregated rows)", path, len(aggregated))
        return path

    def plot_cost_curves(
        self, post_processed_csv: Path, market_prices_csv: Path, clearing: dict[str, Any]
    ) -> Optional[Path]:
        """Write the cost-curve viewer (``cost_curves.html``) from the post-processed furnace-group table.

        Args:
            post_processed_csv: The run's ``post_processed_<timestamp>.csv``.
            market_prices_csv: The run's ``data/market_prices_<start>_<end>.csv``, whose
                ``steel_demand_t`` column gives the engine's steel demand per year. When the
                file or column is missing (older runs) steel clears against realised production,
                as the static chart does without a demand.
            clearing: Output of :func:`~.cost_curves.clearing_config` — the engine's capacity
                limit, clearing shares and shortage premiums.

        Returns:
            The written path, or None when the table is missing or lacks a required column
            (logged as warnings so the plot stage never fails).
        """
        table = self._read_post_processed(post_processed_csv, "cost-curve")
        if table is None:
            return None
        try:
            fgs = cost_curves.furnace_group_rows(table)
        except ValueError as exc:
            logger.warning("%s — skipping the cost-curve viewer", exc)
            return None
        steel_demand = cost_curves.steel_demand_by_year(market_prices_csv)
        demand_source = (
            f"steel demand from {market_prices_csv.name}"
            if steel_demand is not None
            else "steel demand = realised production (no recorded demand)"
        )
        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": f"Furnace-group unit costs and capacity from {post_processed_csv.name}; {demand_source}.",
                "rows": cost_curves.pack_rows(fgs),
                "demand": cost_curves.clearing_table(fgs, steel_demand, clearing),
            },
        }
        path = self._write("cost_curves.html", self._config("Cost curves", clearing=clearing), data)
        logger.info("Wrote cost-curve viewer %s (%d furnace-group rows)", path, len(fgs))
        return path

    def plot_trade_matrix(self, tm_dir: Path) -> Optional[Path]:
        """Write the trade-matrix viewer (``trade_matrix.html``) from the per-year allocation files.

        Args:
            tm_dir: The run's ``TM`` output directory holding ``steel_trade_allocations_<year>.csv``.

        Returns:
            The written path, or None when no allocation file exists or one cannot be read
            (logged as warnings so the plot stage never fails). A year whose file holds no
            metal allocations stays in the viewer's year selector with an empty-state note.
        """
        files = trade_matrix.allocation_files(tm_dir)
        if not files:
            logger.warning("No steel_trade_allocations_<year>.csv under %s — skipping the trade-matrix viewer", tm_dir)
            return None
        try:
            flows = trade_matrix.read_flows(files)
        except ValueError as exc:
            logger.warning("%s — skipping the trade-matrix viewer", exc)
            return None
        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": "Trade-LP allocations from TM/steel_trade_allocations_<year>.csv: steel plant → "
                "demand centre, iron products plant → steelmaking furnace group, ore mine → furnace group.",
                "years": list(files),
                "rows": trade_matrix.pack_rows(flows),
            },
        }
        path = self._write("trade_matrix.html", self._config("Trade matrix"), data)
        logger.info("Wrote trade-matrix viewer %s (%d flows over %d years)", path, len(flows), len(files))
        return path

    def _config(self, chart_title: str, **chart_config: Any) -> dict[str, Any]:
        """The shell's config (colours, geography, runs) plus chart-specific keys."""
        return {
            "chartTitle": chart_title,
            "runs": [self.run_title],
            "defaultRun": self.run_title,
            "techColours": _hex(tech2colours),
            "regionColours": _hex(region2colours),
            "tradeBlocs": trade_bloc_members(self.country_mappings),
            "geoInfo": geo_info(self.country_mappings),
            "geoUnitNames": self.geo_unit_names,
            **chart_config,
        }

    def _write(self, template_name: str, config: dict[str, Any], data: dict[str, Any]) -> Path:
        """Inline plotly.js, the shared shell and the payload into the template; write it under the output dir."""
        html = (
            (ASSETS_DIR / template_name)
            .read_text()
            .replace("__COMMON_CSS__", (ASSETS_DIR / "common.css").read_text())
            .replace("__COMMON_JS__", (ASSETS_DIR / "common.js").read_text())
            .replace("__PLOTLYJS__", get_plotlyjs())
            .replace("__CONFIG__", json.dumps(config))
            .replace("__DATA__", json.dumps(data))
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / template_name
        path.write_text(html)
        return path
