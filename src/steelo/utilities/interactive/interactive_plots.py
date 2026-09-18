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

from . import (
    capacity_production,
    cost_curves,
    emissions,
    metallic_charge_use,
    reductant_use,
    supply_demand,
    trade_allocations,
    trade_matrix,
)

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
        >>> interactive.plot_capacity_and_production(post_processed_csv, demand_centers_json)
        >>> interactive.plot_cost_curves(post_processed_csv, market_prices_csv, clearing)
        >>> interactive.plot_trade_matrix(tm_dir)
        >>> interactive.plot_trade_network(tm_dir)
        >>> interactive.plot_trade_allocations(tm_dir)
        >>> interactive.plot_reductant_use(post_processed_csv, primary_feedstocks_json)
        >>> interactive.plot_metallic_charge_use(post_processed_csv, primary_feedstocks_json, suppliers_json)
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
        self._trade_rows: dict[Path, tuple[list[int], list[dict[str, Any]]]] = {}

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

    def plot_capacity_and_production(
        self, post_processed_csv: Path, demand_centers_json: Optional[Path] = None
    ) -> Optional[Path]:
        """Write the capacity and production viewer (``capacity_and_production.html``).

        Args:
            post_processed_csv: The run's ``post_processed_<timestamp>.csv``.
            demand_centers_json: The prepared ``fixtures/demand_centers.json``, for the
                steel demand overlay — the demand the trade LP is asked to serve, per
                year and country. None (or a missing file) omits the overlay with a
                warning.

        Returns:
            The written path, or None when the table is missing or lacks the capacity or
            production column (logged as warnings so the plot stage never fails).
        """
        from steelo.adapters.repositories.json_repository import DemandCenterJsonRepository

        table = self._read_post_processed(post_processed_csv, "capacity and production")
        if table is None:
            return None
        try:
            aggregated = capacity_production.aggregate_capacity_production(table)
        except ValueError as exc:
            logger.warning("%s — skipping the capacity and production viewer", exc)
            return None

        steel_demand = pd.DataFrame(columns=["year", "geo", "volume_mt"])
        demand_source = ""
        if demand_centers_json is not None and demand_centers_json.is_file():
            centres = DemandCenterJsonRepository(demand_centers_json).list()
            years = {int(year) for year in aggregated["year"]}
            steel_demand = capacity_production.steel_demand_rows(centres, years)
            demand_source = "; steel demand from fixtures/demand_centers.json"
        else:
            logger.warning(
                "No demand centres fixture at %s — steel demand omitted from the capacity and production viewer",
                demand_centers_json,
            )
        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": f"Furnace-group capacity and production from {post_processed_csv.name}{demand_source}.",
                "rows": capacity_production.pack_rows(aggregated),
                "demand": capacity_production.pack_demand(steel_demand),
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

    TRADE_PROVENANCE = (
        "Trade-LP allocations from TM/steel_trade_allocations_<year>.csv: steel plant → demand centre, "
        "iron products plant → steelmaking furnace group, ore mine → furnace group, "
        "scrap supplier → furnace group."
    )

    def _trade_flows(self, tm_dir: Path, viewer: str) -> Optional[tuple[list[int], list[dict[str, Any]]]]:
        """Years and packed country-level flows from the allocation files, read once per plotter.

        Args:
            tm_dir: The run's ``TM`` output directory holding ``steel_trade_allocations_<year>.csv``.
            viewer: Viewer name for the skip warnings.

        Returns:
            ``(years, rows)`` as embedded in the trade viewers, or None (with a warning) when
            no allocation file exists or one cannot be read.
        """
        if tm_dir not in self._trade_rows:
            files = trade_matrix.allocation_files(tm_dir)
            if not files:
                logger.warning(
                    "No steel_trade_allocations_<year>.csv under %s — skipping the %s viewer", tm_dir, viewer
                )
                return None
            try:
                flows = trade_matrix.read_flows(files)
            except ValueError as exc:
                logger.warning("%s — skipping the %s viewer", exc, viewer)
                return None
            self._trade_rows[tm_dir] = (list(files), trade_matrix.pack_rows(flows))
        return self._trade_rows[tm_dir]

    def plot_trade_matrix(self, tm_dir: Path) -> Optional[Path]:
        """Write the trade-matrix viewer (``trade_matrix.html``) from the per-year allocation files.

        Args:
            tm_dir: The run's ``TM`` output directory holding ``steel_trade_allocations_<year>.csv``.

        Returns:
            The written path, or None when no allocation file exists or one cannot be read
            (logged as warnings so the plot stage never fails). A year whose file holds no
            metal allocations stays in the viewer's year selector with an empty-state note.
        """
        flows = self._trade_flows(tm_dir, "trade-matrix")
        if flows is None:
            return None
        years, rows = flows
        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": self.TRADE_PROVENANCE,
                "years": years,
                "rows": rows,
            },
        }
        path = self._write("trade_matrix.html", self._config("Trade matrix"), data)
        logger.info("Wrote trade-matrix viewer %s (%d flows over %d years)", path, len(rows), len(years))
        return path

    def plot_trade_network(self, tm_dir: Path) -> Optional[Path]:
        """Write the trade-network viewer (``trade_network.html``) from the per-year allocation files.

        The network is the trade matrix's sibling rendering: the same country-level flows
        drawn as a chord diagram of who trades with whom, with nodes grouped by country,
        region or trade bloc and sized by trade volume. A map layout places the nodes at
        the allocation-weighted mean position of their plants, suppliers, mines and
        demand centres, embedded here as per-endpoint coordinates.

        Args:
            tm_dir: The run's ``TM`` output directory holding ``steel_trade_allocations_<year>.csv``.

        Returns:
            The written path, or None when no allocation file exists or one cannot be read
            (logged as warnings so the plot stage never fails). A year whose file holds no
            metal allocations stays in the viewer's year selector with an empty-state note.
        """
        flows = self._trade_flows(tm_dir, "trade-network")
        if flows is None:
            return None
        years, rows = flows
        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": self.TRADE_PROVENANCE,
                "years": years,
                "rows": rows,
                "coords": trade_matrix.read_coords(trade_matrix.allocation_files(tm_dir)),
            },
        }
        path = self._write("trade_network.html", self._config("Trade network"), data)
        logger.info("Wrote trade-network viewer %s (%d flows over %d years)", path, len(rows), len(years))
        return path

    def plot_trade_allocations(self, tm_dir: Path) -> Optional[Path]:
        """Write the trade-allocations map viewer (``trade_allocations.html``).

        The map is the per-year pydeck trade maps' replacement: every year's
        allocations as commodity arcs between plants, suppliers and demand centres
        over an inlined world outline, with a year slider, commodity toggles and the
        shared geography filter — one self-contained file with no network access.

        Args:
            tm_dir: The run's ``TM`` output directory holding ``steel_trade_allocations_<year>.csv``.

        Returns:
            The written path, or None when no allocation file exists or one cannot be
            read (logged as warnings so the plot stage never fails). A year whose file
            holds no allocations stays in the year slider with an empty map.
        """
        files = trade_matrix.allocation_files(tm_dir)
        if not files:
            logger.warning(
                "No steel_trade_allocations_<year>.csv under %s — skipping the trade-allocations viewer", tm_dir
            )
            return None
        try:
            years = {year: trade_allocations.records_for_year(path) for year, path in files.items()}
        except ValueError as exc:
            logger.warning("%s — skipping the trade-allocations viewer", exc)
            return None
        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": self.TRADE_PROVENANCE,
                **trade_allocations.pack_years(years),
            },
        }
        config = self._config(
            "Trade allocations",
            commodityColours=trade_allocations.COMMODITY_COLOURS,
            fallbackColour=trade_allocations.FALLBACK_COLOUR,
        )
        path = trade_allocations.write_viewer(config, data, self.output_dir / "trade_allocations.html")
        arcs = sum(len(arc_records) for arc_records, _ in years.values())
        logger.info("Wrote trade-allocations viewer %s (%d arcs over %d years)", path, arcs, len(years))
        return path

    def plot_supply_demand(
        self,
        tm_dir: Path,
        suppliers_json: Optional[Path] = None,
        biomass_availability_json: Optional[Path] = None,
    ) -> Optional[Path]:
        """Write the supply and demand viewer (``supply_demand.html``).

        Args:
            tm_dir: The run's ``TM`` output directory holding ``steel_trade_allocations_<year>.csv``,
                which gives every commodity's use (and the steel demand).
            suppliers_json: The prepared ``fixtures/suppliers.json``, for scrap and ore
                availability. None (or a missing file) omits those availabilities with a warning.
            biomass_availability_json: The prepared ``fixtures/biomass_availability.json``,
                for the CO2 storage limits and biomass budgets. None (or a missing file)
                omits them with a warning.

        Returns:
            The written path, or None when no allocation file exists or one cannot be read
            (logged as warnings so the plot stage never fails).
        """
        from steelo.adapters.repositories.json_repository import (
            BiomassAvailabilityJsonRepository,
            SupplierJsonRepository,
        )

        files = trade_matrix.allocation_files(tm_dir)
        if not files:
            logger.warning("No steel_trade_allocations_<year>.csv under %s — skipping the supply-demand viewer", tm_dir)
            return None
        resolve = supply_demand.geo_resolver(self.country_mappings)
        try:
            used, steel_demand = supply_demand.read_usage(files, resolve)
        except ValueError as exc:
            logger.warning("%s — skipping the supply-demand viewer", exc)
            return None

        suppliers = []
        if suppliers_json is not None and suppliers_json.is_file():
            suppliers = SupplierJsonRepository(suppliers_json).list()
        else:
            logger.warning("No suppliers fixture at %s — scrap and ore availability omitted", suppliers_json)
        biomass_items = []
        if biomass_availability_json is not None and biomass_availability_json.is_file():
            biomass_items = BiomassAvailabilityJsonRepository(biomass_availability_json).list()
        else:
            logger.warning(
                "No biomass availability fixture at %s — CO2 storage and biomass limits omitted",
                biomass_availability_json,
            )
        avail, region_budgets = supply_demand.availability_rows(
            suppliers, biomass_items, self.country_mappings, set(files)
        )
        avail = pd.concat([avail, steel_demand.assign(group="steel", grade="")], ignore_index=True)

        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": "Use and steel demand from TM/steel_trade_allocations_<year>.csv; scrap and ore "
                "availability from fixtures/suppliers.json; CO2 storage and biomass limits from "
                "fixtures/biomass_availability.json.",
                "years": list(files),
                "rows": supply_demand.pack_rows(used),
                "avail": supply_demand.pack_rows(avail),
            },
        }
        config = self._config("Supply and demand", regionBudgets=region_budgets)
        path = self._write("supply_demand.html", config, data)
        logger.info("Wrote supply-demand viewer %s (%d usage rows over %d years)", path, len(used), len(files))
        return path

    def plot_reductant_use(
        self, post_processed_csv: Path, primary_feedstocks_json: Optional[Path] = None
    ) -> Optional[Path]:
        """Write the reductant and energy use viewer (``reductant_use.html``).

        The viewer covers iron production only — steelmaking never uses a reductant.

        Args:
            post_processed_csv: The run's ``post_processed_<timestamp>.csv``, giving each
                furnace group's production, chosen reductant and feedstock allocations.
            primary_feedstocks_json: The prepared ``fixtures/primary_feedstocks.json``
                (the Bill of Materials), whose per-tonne intensities turn the feedstock
                allocations into absolute reductant quantities — only the chosen
                reductant's components count, not auxiliary energy inputs. None (or a
                missing file) omits the reductant-use metrics with a warning, leaving
                the production-by-reductant view.

        Returns:
            The written path, or None when the table is missing, lacks a required column,
            or the Bill of Materials carries an unknown carrier (logged as warnings so
            the plot stage never fails).
        """
        from steelo.adapters.repositories.json_repository import PrimaryFeedstockJsonRepository

        table = self._read_post_processed(post_processed_csv, "reductant-use")
        if table is None:
            return None
        feedstocks = []
        if primary_feedstocks_json is not None and primary_feedstocks_json.is_file():
            feedstocks = PrimaryFeedstockJsonRepository(primary_feedstocks_json).list()
        else:
            logger.warning(
                "No primary feedstocks fixture at %s — carrier use omitted from the reductant viewer",
                primary_feedstocks_json,
            )
        try:
            carriers, aggregated = reductant_use.aggregate_reductant_use(table, feedstocks)
        except ValueError as exc:
            logger.warning("%s — skipping the reductant-use viewer", exc)
            return None
        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": f"Iron production and chosen reductants from {post_processed_csv.name}; reductant "
                "quantities = feedstock allocations × the chosen reductant's Bill of Materials "
                "intensities (fixtures/primary_feedstocks.json).",
                "rows": reductant_use.pack_rows(aggregated, carriers),
            },
        }
        config = self._config(
            "Reductant and energy use",
            carriers=reductant_use.carrier_meta(carriers),
            reductantColours=reductant_use.REDUCTANT_COLOURS,
        )
        path = self._write("reductant_use.html", config, data)
        logger.info(
            "Wrote reductant-use viewer %s (%d aggregated rows, %d carriers)", path, len(aggregated), len(carriers)
        )
        return path

    def plot_metallic_charge_use(
        self,
        post_processed_csv: Path,
        primary_feedstocks_json: Optional[Path] = None,
        suppliers_json: Optional[Path] = None,
    ) -> Optional[Path]:
        """Write the metallic charge use viewer (``metallic_charge_use.html``).

        The viewer shows the metallic charges steelmakers and ironmakers feed into
        their products, from the table's per-feedstock allocation rows (a furnace
        group running several charges at different shares contributes each share to
        its own charge), with an optional local scrap supply overlay.

        Args:
            post_processed_csv: The run's ``post_processed_<timestamp>.csv``, whose
                feedstock rows carry each furnace group's per-charge demand.
            primary_feedstocks_json: The prepared ``fixtures/primary_feedstocks.json``
                (the Bill of Materials), whose ``metallic_charge`` fields identify
                which feedstock rows are charges. Required — None (or a missing file)
                skips the viewer with a warning.
            suppliers_json: The prepared ``fixtures/suppliers.json``, for the local
                scrap supply overlay. None (or a missing file) omits the overlay
                with a warning.

        Returns:
            The written path, or None when the table or the Bill of Materials is
            missing, or a required column is absent (logged as warnings so the plot
            stage never fails).
        """
        from steelo.adapters.repositories.json_repository import PrimaryFeedstockJsonRepository, SupplierJsonRepository

        table = self._read_post_processed(post_processed_csv, "metallic-charge")
        if table is None:
            return None
        if primary_feedstocks_json is None or not primary_feedstocks_json.is_file():
            logger.warning(
                "No primary feedstocks fixture at %s — skipping the metallic-charge viewer (charges "
                "cannot be identified without the Bill of Materials)",
                primary_feedstocks_json,
            )
            return None
        feedstocks = PrimaryFeedstockJsonRepository(primary_feedstocks_json).list()
        try:
            aggregated = metallic_charge_use.aggregate_charge_use(table, feedstocks)
        except ValueError as exc:
            logger.warning("%s — skipping the metallic-charge viewer", exc)
            return None

        suppliers = []
        if suppliers_json is not None and suppliers_json.is_file():
            suppliers = SupplierJsonRepository(suppliers_json).list()
        else:
            logger.warning("No suppliers fixture at %s — local scrap supply overlay omitted", suppliers_json)
        years = {int(year) for year in aggregated["year"]}
        supply = metallic_charge_use.scrap_supply_rows(suppliers, self.country_mappings, years)

        data = {
            self.run_title: {
                "title": self.run_title,
                "provenance": f"Per-feedstock charge allocations from {post_processed_csv.name}; charges "
                "identified by the Bill of Materials (fixtures/primary_feedstocks.json); local scrap "
                "supply from fixtures/suppliers.json.",
                "rows": metallic_charge_use.pack_rows(aggregated),
                "supply": metallic_charge_use.pack_supply(supply),
            },
        }
        config = self._config(
            "Metallic charge use",
            chargeColours=metallic_charge_use.CHARGE_COLOURS,
            chargeOrder=metallic_charge_use.CHARGE_ORDER,
        )
        path = self._write("metallic_charge_use.html", config, data)
        logger.info(
            "Wrote metallic-charge viewer %s (%d aggregated rows, %d scrap supply rows)",
            path,
            len(aggregated),
            len(supply),
        )
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
