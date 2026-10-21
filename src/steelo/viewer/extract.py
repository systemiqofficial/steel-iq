"""Write the scenario viewer's per-run files into ``<run>/viewer/``, from files on disk only.

timeseries.csv comes from the run's post_processed_*.csv (one row per
furnace group x year, repeated once per metallic feedstock, so furnace-group-level
fields are deduplicated on (year, furnace_group_id) before summing). It holds
one row per year x geo_key x product x technology with capacity, production and
all nine emissions accountings (three boundaries x direct / direct-with-biogenic
/ indirect), everything in kt.

charge.csv (one row per year x geo_key x product x technology x charge, use in
kt) is lifted from the DATA block of the run's metallic_charge_use.html,
whose rows are already aggregated with the Bill-of-Materials filter deciding
which feedstock rows count as metallic charges, so the compare pane matches the
per-run page by construction. reductant.csv (one row per year x geo_key x
technology x reductant route, iron only, with the route's iron production in Mt
and the chosen reductant's carrier quantities in unit-suffixed columns, Mt for
mass carriers / TWh for energy ones, auxiliary energy excluded) is likewise
lifted from reductant_use.html; carriers all-zero in a run are absent from its
HTML and filled with 0 so every run shares one schema. supply.csv (one
row per year x geo x commodity x grade with use_mt and avail_mt) is lifted from
supply_demand.html: steel consumption at demand-centre countries, scrap/ore
drawn vs supplier capacity at source countries (ore by grade), CO2 stored vs
per-country storage limits and bio-PCI use at consuming countries. Two
amendments to the page's data: steel demand is read from the run's master
workbook for the run's demand scenario, because the page's allocation-file
demand misses centres that went entirely unserved; and biomass budget rows keep
their region: geographies, with the region membership map lifted into geo.json
so the compare pane can count a budget once any member country is selected.
geo.json (trade blocs, country names and regions, geo-unit display names, region
budgets) is lifted from the CONFIG blocks of emissions.html and
supply_demand.html, so the compare pane's geography filter matches the run's
pages exactly.

greenfield.csv (one row per build year x geo_key x product x technology with
the number of new furnace groups and their capacity in kt) is derived from the
same post_processed CSV: a furnace group is a greenfield build when it first
appears after the start year on a greenfield plant, one ever recorded in an
``indi_`` plant group, or whose root furnace group (id equal to the plant id)
itself first appears after the start year. The latter catches credit-funded
greenfields that the capacity policy moves into the funding company's plant
group; only greenfield plants carry an unsuffixed root group. post_processed
holds operating groups only, so first appearance is the first operating year.
trade.csv (one row per year x product x commodity x origin x destination
with the delivered volume in Mt, technology summed away, same-country rows
included) is lifted from trade_matrix.html.

annual_costs.csv (one row per year x geo_key x product x technology x reductant with
production-weighted cost totals in million USD: the post_processed
cost_breakdown components, the material cost per metallic feedstock
(material_<feedstock>), fixed opex, debt service, carbon cost, embedded
subsidies, the model's own unit-cost total as a check and the installed capex,
plus furnace-group counts, capacity and production in kt) feeds the annual
costs pane and is derived from the same post_processed CSV. The model's unit
production cost is the breakdown + fopex + debt + carbon (checked here). Capex is
not exported, so it is read off the debt schedule: within a financing cycle the
debt payment per tonne of capacity falls by exactly cost_of_debt x annual
principal each year, so consecutive years give the principal and capex =
principal x lifetime / (1 - equity_share). Cycle resets (renovations, switches,
capacity changes), single-year cycles and expired loans fall back to the
master's greenfield capex (x renovation share for incumbents) net of the CSV's
capex subsidy. A furnace group that switched technology before its old loan
was repaid keeps paying that loan on top of the new one (the model's legacy
debt schedule), which the slope would add to the new capex: such cycles are
recognised by an inferred capex well above the greenfield value or a first
payment above a fresh loan's, re-based on the master's capex, and the old
loan's payments split out as debt_legacy, the plant's cost, not the
technology's. Needs the run's simulation_config.json (lifetime, equity
share) and its master workbook (Cost of capital and Techno-economic details
sheets, for the cost of debt and the greenfield / renovation capex).

inputs.json holds the run's View data rows per topic, read from the master's
sheets in TOPICS for the run's demand, scrap and grid scenarios, undiffed.
results/ holds the run's own tables for the Download results pane, gzipped and
paged. run.json, written last, names the run and lists what was written.
"""

import argparse
import csv
import gzip
import importlib.metadata
import json
import logging
import math
import re
import shutil
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pandas as pd

logger = logging.getLogger(__name__)

# Prefixes: rs = rs-inspired, wsn / wso = worldsteel without / with optional credits
EMISSION_COLUMNS = {
    "emissions_rs-inspired_direct_ghg": "rs_direct",
    "emissions_rs-inspired_direct_with_biomass_ghg": "rs_direct_bio",
    "emissions_rs-inspired_indirect_ghg": "rs_indirect",
    "emissions_worldsteel_no_opt_credits_direct_ghg": "wsn_direct",
    "emissions_worldsteel_no_opt_credits_direct_with_biomass_ghg": "wsn_direct_bio",
    "emissions_worldsteel_no_opt_credits_indirect_ghg": "wsn_indirect",
    "emissions_worldsteel_opt_credits_direct_ghg": "wso_direct",
    "emissions_worldsteel_opt_credits_direct_with_biomass_ghg": "wso_direct_bio",
    "emissions_worldsteel_opt_credits_indirect_ghg": "wso_indirect",
}

SOURCE_COLUMNS = [
    "year",
    "geo_key",
    "furnace_group_id",
    "technology",
    "product",
    "capacity",
    "production",
    *EMISSION_COLUMNS,
]

VIEWER_DIR = "viewer"
PAGES_DIR = Path("plots") / "interactive"
TIMESERIES_NAME = "timeseries.csv"
CHARGE_NAME = "charge.csv"
REDUCTANT_NAME = "reductant.csv"
SUPPLY_NAME = "supply.csv"
GREENFIELD_NAME = "greenfield.csv"
TRADE_NAME = "trade.csv"
ANNUAL_COSTS_NAME = "annual_costs.csv"
GEO_NAME = "geo.json"
INPUTS_NAME = "inputs.json"
RUN_NAME = "run.json"
SCHEMA_VERSION = 1

# Furnace-group-level post_processed columns, repeated on every feedstock row
ANNUAL_COSTS_FG_COLUMNS = [
    "year",
    "iso3",
    "geo_key",
    "furnace_group_id",
    "technology",
    "product",
    "chosen_reductant",
    "capacity",
    "production",
    "unit_fopex",
    "unit_debt_repayment",
    "unit_carbon_cost",
    "unit_production_cost",
    "unit_subsidy_total",
    "unit_subsidy_capex",
    "commands",
]
ANNUAL_COSTS_BREAKDOWN_PREFIX = "cost_breakdown - "
ANNUAL_COSTS_MATERIAL_COLUMN = ANNUAL_COSTS_BREAKDOWN_PREFIX + "material cost (incl. transport and tariffs)"
# ProductionThreshold.low: the model spreads unit fopex and debt over max(util, 0.1) x capacity.
ANNUAL_COSTS_UTIL_FLOOR = 0.1
# Above this multiple of greenfield capex, the payment slope carries a previous technology's loan
ANNUAL_COSTS_LEGACY_FACTOR = 1.08

GREENFIELD_COLUMNS = [
    "year",
    "plant_id",
    "plant_group_id",
    "furnace_group_id",
    "geo_key",
    "product",
    "technology",
    "capacity",
]

# Mass carriers in Mt, energy carriers in TWh, as reductant_use.html embeds them
CARRIER_COLUMNS = {
    "coking_coal": "coking_coal_mt",
    "pci": "pci_mt",
    "bio_pci": "bio_pci_mt",
    "hydrogen": "hydrogen_mt",
    "natural_gas": "natural_gas_twh",
    "coal": "coal_twh",
    "electricity": "electricity_twh",
}


def extract_timeseries(source_csv: Path) -> pd.DataFrame:
    """Aggregate one post_processed CSV to the tidy per-run time series.

    Args:
        source_csv: Path of the run's post_processed_*.csv.

    Returns:
        Frame with one row per year x geo_key x product x technology and kt
        sums of capacity, production and the nine emissions accountings.

    Notes:
        Rows repeat per metallic feedstock, so furnace-group-level fields are
        deduplicated on (year, furnace_group_id) first. Tonnes become kt.
        Country and region are derived downstream from geo_key + geo.json.
    """
    df = pd.read_csv(source_csv, usecols=SOURCE_COLUMNS)
    fg = df.drop_duplicates(["year", "furnace_group_id"])
    value_columns = {"capacity": "capacity_kt", "production": "production_kt", **EMISSION_COLUMNS}
    agg = fg.groupby(["year", "geo_key", "product", "technology"], as_index=False).agg(
        **{out: (src, "sum") for src, out in value_columns.items()}
    )
    for col in value_columns.values():
        agg[col] = (agg[col] / 1e3).round(2)
    return agg


def extract_greenfield(source_csv: Path) -> pd.DataFrame:
    """Greenfield builds derived from one post_processed CSV.

    Args:
        source_csv: Path of the run's post_processed_*.csv.

    Returns:
        Frame with one row per build year x geo_key x product x technology,
        the number of new furnace groups and their capacity in kt.

    Notes:
        post_processed holds operating furnace groups only, so a group's first
        year of appearance is its first operating year — the build year. A
        group is a greenfield build when that year is after the start year and
        its plant is a greenfield plant: ever recorded in an ``indi_`` plant
        group, or carrying a root furnace group (id equal to the plant id)
        that itself first appears after the start year. The root-group test
        catches credit-funded greenfields the capacity policy moves into the
        funding company's plant group — incumbent plants only ever carry
        ``_N``-suffixed groups. Greenfield plants nearly always hold a
        single furnace group, so the group count is the plant count in
        practice (a later second unit on the same plant counts again).
    """
    df = pd.read_csv(source_csv, usecols=GREENFIELD_COLUMNS)
    fg = df.drop_duplicates(["year", "furnace_group_id"])
    first = fg.groupby("furnace_group_id")["year"].transform("min")
    debut = fg[fg["year"] == first]
    late = debut[debut["year"] > debut["year"].min()]
    indi_plants = set(fg.loc[fg["plant_group_id"].fillna("").str.startswith("indi_"), "plant_id"])
    root_plants = set(late.loc[late["furnace_group_id"] == late["plant_id"], "plant_id"])
    builds = late[late["plant_id"].isin(indi_plants | root_plants)]
    agg = builds.groupby(["year", "geo_key", "product", "technology"], as_index=False).agg(
        n_plants=("furnace_group_id", "count"), capacity_kt=("capacity", "sum")
    )
    agg["capacity_kt"] = (agg["capacity_kt"] / 1e3).round(1)
    return agg


def extract_trade(pages_dir: Path) -> pd.DataFrame | None:
    """Lift one run's trade flows from its trade_matrix.html.

    Args:
        pages_dir: The run's interactive plot folder.

    Returns:
        Frame with one row per year x product x commodity x origin x
        destination and the delivered volume in Mt, or None when the HTML (or
        its DATA block) is missing.

    Notes:
        The HTML embeds year x product x commodity x origin x destination x
        producing-technology rows; technology is summed away here. Same-country
        rows (origin == destination) are the local deliveries and are kept.
    """
    lifted = lift_html_data(pages_dir, "trade_matrix.html")
    if lifted is None:
        return None
    rows = pd.DataFrame(
        [(r["y"], r["p"], r["c"], r["o"], r["d"], r["v"]) for r in lifted[1]["rows"]],
        columns=["year", "product", "commodity", "origin", "destination", "volume_mt"],
    )
    # as_index=False keeps a frame; pandas-stubs types the column sum as a Series
    agg = cast(
        pd.DataFrame,
        rows.groupby(["year", "product", "commodity", "origin", "destination"], as_index=False)["volume_mt"].sum(),
    )
    agg["volume_mt"] = agg["volume_mt"].round(4)
    return agg


def lift_html_data(pages_dir: Path, html_name: str) -> tuple[dict, dict] | None:
    """The CONFIG block and first run's DATA payload of one plot HTML.

    Args:
        pages_dir: The run's interactive plot folder.
        html_name: The plot HTML file name.

    Returns:
        (CONFIG, payload) dicts, or None when the HTML or either block is
        missing. A run's pages embed a single run; a multi-run page is warned
        about and its first payload used.
    """
    html = pages_dir / html_name
    if not html.exists():
        return None
    text = html.read_text(encoding="utf-8")
    cfg = re.search(r"const CONFIG = (\{.*?\});\n", text, re.S)
    data = re.search(r"const DATA = (\{.*?\});\n", text, re.S)
    if not (cfg and data):
        return None
    payloads = list(json.loads(data.group(1)).values())
    if len(payloads) > 1:
        logger.warning(f"{html_name} embeds {len(payloads)} runs, using the first")
    return json.loads(cfg.group(1)), payloads[0]


def extract_charge(pages_dir: Path) -> pd.DataFrame | None:
    """Lift one run's metallic charge rows from its plot HTML.

    Args:
        pages_dir: The run's interactive plot folder.

    Returns:
        Frame with one row per year x geo_key x product x technology x charge
        and the charged tonnage in kt, or None when the HTML (or its DATA
        block) is missing.

    Notes:
        The HTML embeds the rows already aggregated to this grain, in Mt, with
        the Bill of Materials deciding which feedstock rows count as charges
        (reductant rows like bio_pci are out) — reusing them keeps the compare
        pane identical to the per-run page.
    """
    lifted = lift_html_data(pages_dir, "metallic_charge_use.html")
    if lifted is None:
        return None
    rows = pd.DataFrame(
        [(r["y"], r["g"], r["p"], r["t"], r["c"], round(r["v"] * 1e3, 1)) for r in lifted[1]["rows"]],
        columns=["year", "geo_key", "product", "technology", "charge", "use_kt"],
    )
    return rows.sort_values(["year", "geo_key", "product", "technology", "charge"])


def extract_reductant(pages_dir: Path) -> pd.DataFrame | None:
    """Lift one run's reductant use rows from its plot HTML.

    Args:
        pages_dir: The run's interactive plot folder.

    Returns:
        Frame with one row per year x geo_key x technology x reductant route
        (iron rows only), the route's iron production in Mt and the chosen
        reductant's carrier quantities in the CARRIER_COLUMNS units, or None
        when the HTML (or its DATA/CONFIG block) is missing.

    Raises:
        ValueError: If the HTML embeds a carrier CARRIER_COLUMNS does not name
            (schema drift — add its column).

    Notes:
        Only the chosen reductant's components count as use — auxiliary energy
        of the same process is excluded, as on the per-run page. Carriers
        all-zero in a run are absent from its HTML and filled with 0 here.
    """
    lifted = lift_html_data(pages_dir, "reductant_use.html")
    if lifted is None:
        return None
    cfg, payload = lifted
    carriers = [c["key"] for c in cfg["carriers"]]
    unknown = [c for c in carriers if c not in CARRIER_COLUMNS]
    if unknown:
        raise ValueError(
            f"reductant_use.html: unknown reductant carrier(s) {', '.join(unknown)} — add them to CARRIER_COLUMNS"
        )
    rows = pd.DataFrame(
        [
            (
                r["y"],
                r["g"],
                r["t"],
                r["r"],
                r["pr"],
                *[dict(zip(carriers, r["e"])).get(k, 0.0) for k in CARRIER_COLUMNS],
            )
            for r in payload["rows"]
        ],
        columns=["year", "geo_key", "technology", "reductant", "production_mt", *CARRIER_COLUMNS.values()],
    )
    return rows.sort_values(["year", "geo_key", "technology", "reductant"])


DEMAND_SHEET = "Demand and scrap availability"
DEMAND_METRIC = "Crude steel consumption for forming [kt]"


def steel_demand_table(master: Path, scenario: str, cache: dict) -> pd.DataFrame:
    """True per-country steel demand from a master workbook's demand sheet.

    Args:
        master: The master Excel the run was prepared from.
        scenario: The run's demand scenario (a Scenario column value, e.g. BAU).
        cache: Reuse dict keyed by (master, scenario) — the sheet is slow to read.

    Returns:
        Columns ``year, geo_key, avail_mt``: the scenario's crude steel
        consumption per ISO3 and year, zero-demand rows dropped.

    Raises:
        ValueError: If the sheet has no demand rows for the scenario.
    """
    key = (str(master), scenario)
    if key not in cache:
        sheet = pd.read_excel(master, sheet_name=DEMAND_SHEET)
        sheet["Metric"] = sheet["Metric"].str.strip()
        rows = sheet[(sheet["Metric"] == DEMAND_METRIC) & (sheet["Scenario"] == scenario)]
        if rows.empty:
            raise ValueError(f"{master.name}: no '{DEMAND_METRIC}' rows for scenario {scenario!r}")
        years = [c for c in sheet.columns if isinstance(c, (int, float))]
        long = rows.melt(id_vars=["ISO-3 code"], value_vars=years, var_name="year", value_name="kt")
        long["year"] = long["year"].astype(int)
        long = long.rename(columns={"ISO-3 code": "geo_key"})
        long["avail_mt"] = (long["kt"] / 1e3).round(4)
        cache[key] = long.loc[long["avail_mt"] > 0, ["year", "geo_key", "avail_mt"]]
    return cache[key]


def extract_supply(pages_dir: Path, demand: pd.DataFrame) -> pd.DataFrame | None:
    """Lift one run's supply & demand rows from its plot HTML.

    Args:
        pages_dir: The run's interactive plot folder.
        demand: The run's steel demand, :func:`steel_demand_table` output.

    Returns:
        Frame with one row per year x geo_key x commodity x grade and the used
        and available volumes in Mt, or None when the HTML (or its DATA block)
        is missing.

    Notes:
        Use and availability count at different geographies per commodity:
        steel consumption vs demand at demand-centre countries, scrap and ore
        (by grade) drawn vs supplier capacity at source countries, CO2 stored
        vs per-country limits and bio-PCI use at consuming countries. Steel
        demand comes from the run's master workbook — the page's own demand
        misses centres that went entirely unserved. Biomass budgets belong to
        whole TIAM-UCL regions, so their availability rows carry region:
        geographies (kept out of the compare pane's geography filter and
        matched by region membership instead).
    """
    lifted = lift_html_data(pages_dir, "supply_demand.html")
    if lifted is None:
        return None

    def frame(records: list[dict], column: str) -> pd.DataFrame:
        return pd.DataFrame(
            [(r["y"], r["g"], r["c"], r.get("s") or "", r["v"]) for r in records],
            columns=["year", "geo_key", "commodity", "grade", column],
        )

    use = frame(lifted[1]["rows"], "use_mt")
    avail = frame(lifted[1]["avail"], "avail_mt")
    years = set(use["year"]) | set(avail["year"])
    steel = demand[demand["year"].isin(years)].copy()
    steel[["commodity", "grade"]] = ["steel", ""]
    avail = pd.concat([avail[avail["commodity"] != "steel"], steel[avail.columns.tolist()]])

    merged = use.merge(avail, on=["year", "geo_key", "commodity", "grade"], how="outer", validate="one_to_one").fillna(
        0
    )
    return merged.sort_values(["year", "geo_key", "commodity", "grade"])


def extract_geo(pages_dir: Path) -> dict | None:
    """The run's geography structures, from the CONFIG blocks of its plot HTMLs.

    Args:
        pages_dir: The run's interactive plot folder.

    Returns:
        ``{"tradeBlocs", "geoInfo", "geoUnitNames", "regionBudgets"}``, or None
        when emissions.html (or its CONFIG block) is missing.

    Notes:
        The plot HTMLs embed tradeBlocs / geoInfo / geoUnitNames (built by the
        simulation's interactive-plot writer); reusing them keeps the compare
        pane's geography filter identical to the run's pages. The TIAM-UCL
        region membership map (regionBudgets) comes from supply_demand.html,
        for the bio budgets' region matching, and is empty without that page.
    """
    html = pages_dir / "emissions.html"
    if not html.exists():
        return None
    m = re.search(r"const CONFIG = (\{.*?\});\n", html.read_text(encoding="utf-8"), re.S)
    if not m:
        return None
    cfg = json.loads(m.group(1))
    geo = {key: cfg[key] for key in ("tradeBlocs", "geoInfo", "geoUnitNames")}
    lifted = lift_html_data(pages_dir, "supply_demand.html")
    geo["regionBudgets"] = lifted[0]["regionBudgets"] if lifted else {}
    return geo


def annual_costs_master_inputs(master: Path, cache: dict) -> tuple[dict, dict, float]:
    """Financing and capex inputs of a master workbook, for the capex inference.

    Args:
        master: The master Excel the run was prepared from.
        cache: Reuse dict keyed by master path — the sheets are slow to read.

    Returns:
        ``(cost_of_debt, greenfield_capex, renovation_share)``: cost of debt
        per (ISO3, technology) from the Cost of capital sheet; greenfield capex
        in USD per tonne of capacity per technology and the renovation capex
        share from the Techno-economic details sheet.

    Raises:
        ValueError: If technologies carry different renovation shares.
    """
    key = str(master)
    if key not in cache:
        coc = pd.read_excel(master, sheet_name="Cost of capital").dropna(subset=["ISO-3 Code", "Tech"])
        cost_of_debt = {(str(r["ISO-3 Code"]), str(r["Tech"])): float(r["Cost of debt"]) for _, r in coc.iterrows()}
        tech = pd.read_excel(master, sheet_name="Techno-economic details").dropna(subset=["Technology", "Metric"])
        greenfield = {
            str(r["Technology"]): float(r["Value"]) for _, r in tech[tech["Metric"] == "Greenfield capex"].iterrows()
        }
        shares = set(tech.loc[tech["Metric"] == "Renovation capex", "Value"].astype(float))
        if len(shares) != 1:
            raise ValueError(f"{master.name}: expected one renovation capex share, got {shares}")
        cache[key] = (cost_of_debt, greenfield, shares.pop())
    return cache[key]


def annual_costs_furnace_groups(source_csv: Path) -> tuple[pd.DataFrame, list[str]]:
    """Per furnace-group-year unit costs of one post_processed CSV, breakdown flattened.

    Args:
        source_csv: Path of the run's post_processed_*.csv.

    Returns:
        ``(frame, components)``: one row per producing furnace group x year with
        the furnace-group columns, one column per cost component (USD/t) and
        the component names — the non-material cost_breakdown keys plus one
        ``material_<feedstock>`` per metallic feedstock.

    Raises:
        ValueError: If the per-feedstock material costs do not sum to the
            material column, or the components + fopex + debt + carbon do not
            reproduce unit_production_cost (a booking change upstream).

    Notes:
        Energy, flux and by-product columns are already weighted by each
        pathway's product share, so a furnace group's feedstock rows sum to its
        per-tonne value; the material column is kept per feedstock instead.
        Only producing groups count: an idle operating group carries a zero
        unit cost by the model's definition.
    """
    head = pd.read_csv(source_csv, nrows=0).columns
    breakdown_cols = [
        c
        for c in head
        if c.startswith(ANNUAL_COSTS_BREAKDOWN_PREFIX) and c != ANNUAL_COSTS_BREAKDOWN_PREFIX + "demand_share_pct"
    ]
    df = pd.read_csv(source_csv, usecols=ANNUAL_COSTS_FG_COLUMNS + ["feedstock"] + breakdown_cols, low_memory=False)
    key = ["year", "furnace_group_id"]
    fg = df.drop_duplicates(key)[ANNUAL_COSTS_FG_COLUMNS].copy()
    other_cols = [c for c in breakdown_cols if c != ANNUAL_COSTS_MATERIAL_COLUMN]
    breakdown = df.groupby(key)[other_cols].sum(min_count=1).reset_index()
    materials = df.dropna(subset=["feedstock"]).pivot_table(
        index=key, columns="feedstock", values=ANNUAL_COSTS_MATERIAL_COLUMN, aggfunc="sum", fill_value=0.0
    )
    materials.columns = [f"material_{c}" for c in materials.columns]
    material_cols = sorted(materials.columns)
    total_material = df.groupby(key)[ANNUAL_COSTS_MATERIAL_COLUMN].sum().reset_index()
    fg = (
        fg.merge(breakdown, on=key, how="left")
        .merge(materials.reset_index(), on=key, how="left")
        .merge(total_material, on=key)
    )
    fg = fg[fg["production"] > 0].copy()
    fg["chosen_reductant"] = fg["chosen_reductant"].fillna("")
    fg[other_cols + material_cols] = fg[other_cols + material_cols].fillna(0.0)
    split_gap = (fg[material_cols].sum(axis=1) - fg[ANNUAL_COSTS_MATERIAL_COLUMN]).abs().max()
    if split_gap > 1e-6:
        raise ValueError(
            f"{source_csv.name}: per-feedstock material costs do not sum to the material column (max gap {split_gap})"
        )
    fg = fg.drop(columns=[ANNUAL_COSTS_MATERIAL_COLUMN])
    components = [c[len(ANNUAL_COSTS_BREAKDOWN_PREFIX) :] for c in other_cols] + material_cols
    fg = fg.rename(columns=dict(zip(other_cols, components)))
    recomposed = fg[components].sum(axis=1) + fg["unit_fopex"] + fg["unit_debt_repayment"] + fg["unit_carbon_cost"]
    weight = fg["production"]
    gap = ((recomposed - fg["unit_production_cost"]).abs() * weight).sum() / (
        fg["unit_production_cost"].abs() * weight
    ).sum()
    if gap > 1e-4:
        raise ValueError(
            f"{source_csv.name}: breakdown + fopex + debt + carbon != unit_production_cost "
            f"(production-weighted gap {gap:.2e})"
        )
    return fg, components


def annual_costs_infer_capex(
    fg: pd.DataFrame, cost_of_debt: dict, greenfield: dict, renovation_share: float, lifetime: int, equity_share: float
) -> pd.DataFrame:
    """Add the subsidised capex per tonne of capacity to each furnace-group-year.

    Args:
        fg: Output of :func:`annual_costs_furnace_groups`.
        cost_of_debt: ``{(iso3, technology): cost_of_debt}``.
        greenfield: Greenfield capex per technology (USD/t capacity).
        renovation_share: Renovation capex as a share of greenfield.
        lifetime: Plant lifetime / financing cycle in years.
        equity_share: Equity share of every investment.

    Returns:
        The frame with ``capex_tcap`` (USD per tonne of capacity), ``capex_source``
        ("inferred" | "fallback" | "legacy-split"),
        ``legacy_pay_tcap`` (a previous technology's loan payment per tonne of
        capacity, 0 without one) and ``legacy_bad`` (the split left a clearly
        negative residual) added, sorted by furnace group and year.

    Raises:
        ValueError: If a country x technology has no cost of debt, or a
            technology no greenfield capex, in the master.

    Notes:
        The model's debt service is straight-line principal plus interest on
        the average balance, so within one cycle the payment per tonne of
        capacity (unit_debt_repayment x max(util, 0.1)) drops by exactly
        cost_of_debt x annual principal each year: the drop between consecutive
        years gives the principal, and capex = principal x lifetime /
        (1 - equity_share). A cycle ends where the payment rises or a
        renovation / switch command lands (a renovation loan can be smaller
        than the expiring one), the technology or capacity changes, a year is
        skipped or the payment is zero (loan expired). Single-year cycles fall
        back to the master's greenfield capex — x renovation share for
        incumbents (first seen in the start year) — net of the CSV's capex
        subsidy.

        Legacy debt: a group that switched technology before its old loan was
        repaid keeps paying it on top of the new loan, so the slope adds the
        old principal (a BF to DRI switch reads 860 + 660). Cycles
        whose inferred capex exceeds the greenfield value by more than
        ANNUAL_COSTS_LEGACY_FACTOR, or whose first payment exceeds a fresh loan's, are
        re-based on the master's capex net of the CSV's subsidy; the legacy
        payment is the observed debt service minus the fresh loan's schedule
        counted from the cycle's first year.
    """
    fg = fg.sort_values(["furnace_group_id", "year"]).copy()
    util = fg["production"] / fg["capacity"]
    fg["pay_tcap"] = fg["unit_debt_repayment"] * util.clip(lower=ANNUAL_COSTS_UTIL_FLOOR)
    rates = [cost_of_debt.get((iso3, tech)) for iso3, tech in zip(fg["iso3"], fg["technology"])]
    missing = sorted({(iso3, tech) for (iso3, tech), r in zip(zip(fg["iso3"], fg["technology"]), rates) if r is None})
    if missing:
        raise ValueError(f"no cost of debt for {missing[:10]}")
    fg["cost_of_debt"] = rates

    g = fg.groupby("furnace_group_id")
    reset = fg["commands"].fillna("").str.contains("RenovateFurnaceGroup|SwitchingTechnology")
    same_cycle = (
        (g["year"].diff() == 1)
        & (g["technology"].shift() == fg["technology"])
        & (g["capacity"].shift() == fg["capacity"])
        & (fg["pay_tcap"] > 0)
        & (g["pay_tcap"].diff() < 0)
        & ~reset
    )
    fg["cycle"] = (~same_cycle).cumsum()
    principal = (-g["pay_tcap"].diff() / fg["cost_of_debt"]).where(same_cycle)
    principal = principal.groupby(fg["cycle"]).transform("median")
    fg["capex_tcap"] = principal * lifetime / (1.0 - equity_share)
    fg["capex_source"] = "inferred"

    first_year = g["year"].transform("min")
    fallback = fg["technology"].map(greenfield)
    unknown = sorted(fg.loc[fallback.isna(), "technology"].unique())
    if unknown:
        raise ValueError(f"no greenfield capex for technologies {unknown}")
    fallback = fallback.where(first_year > fg["year"].min(), fallback * renovation_share)
    fallback = fallback - fg["unit_subsidy_capex"].fillna(0.0)
    need = fg["capex_tcap"].isna()
    fg.loc[need, "capex_tcap"] = fallback[need]
    fg.loc[need, "capex_source"] = "fallback"

    greenfield_tcap = fg["technology"].map(greenfield) - fg["unit_subsidy_capex"].fillna(0.0)
    contaminated = fg["capex_tcap"] > ANNUAL_COSTS_LEGACY_FACTOR * greenfield_tcap
    fg.loc[contaminated, "capex_tcap"] = greenfield_tcap[contaminated]
    fg.loc[contaminated, "capex_source"] = "legacy-split"
    years_in = fg["year"] - fg.groupby("cycle")["year"].transform("min")
    fresh_pay = (
        fg["capex_tcap"] * (1 - equity_share) / lifetime * (1 + fg["cost_of_debt"] * (lifetime - years_in + 0.5))
    )
    excess = fg["pay_tcap"] - fresh_pay
    flagged = fg.loc[contaminated | ((years_in == 0) & (excess > 0.05 * fresh_pay)), "cycle"]
    legacy_cycle = fg["cycle"].isin(set(flagged))
    fg["legacy_pay_tcap"] = excess.where(legacy_cycle, 0.0).clip(lower=0.0)
    fg["legacy_bad"] = legacy_cycle & (excess < -0.5)
    return fg


def extract_annual_costs(source_csv: Path, master: Path, config: dict) -> pd.DataFrame:
    """Annual-cost rows of one run (see the module docstring for the accounting).

    Args:
        source_csv: Path of the run's post_processed_*.csv.
        master: The master Excel the run was prepared from.
        config: The run's simulation_config.json, for the lifetime and equity share.

    Returns:
        Frame with one row per year x geo_key x product x technology x
        reductant: ``n_fg``, ``n_fg_inferred``, ``capacity_kt``,
        ``production_kt`` and production-weighted totals in million USD for
        every cost component, ``fopex``, ``debt`` (the current technology's
        loan), ``debt_legacy`` (a previous technology's loan still being
        repaid), ``carbon``, ``subsidy``, ``unit_cost_check`` and ``capex``.
    """
    lifetime, equity_share = int(config["plant_lifetime"]), float(config["equity_share"])
    cost_of_debt, greenfield, renovation_share = annual_costs_master_inputs(master, {})
    fg, components = annual_costs_furnace_groups(source_csv)
    fg = annual_costs_infer_capex(fg, cost_of_debt, greenfield, renovation_share, lifetime, equity_share)

    prod = fg["production"]
    out = (
        fg[["year", "geo_key", "product", "technology", "chosen_reductant"]]
        .rename(columns={"chosen_reductant": "reductant"})
        .copy()
    )
    out["n_fg"] = 1
    out["n_fg_inferred"] = (fg["capex_source"] == "inferred").astype(int)
    out["capacity_kt"] = fg["capacity"] / 1e3
    out["production_kt"] = prod / 1e3
    for c in components:
        out[c] = fg[c] * prod / 1e6
    out["fopex"] = fg["unit_fopex"] * prod / 1e6
    util = (prod / fg["capacity"]).clip(lower=ANNUAL_COSTS_UTIL_FLOOR)
    debt_legacy = fg["legacy_pay_tcap"] / util * prod / 1e6
    out["debt"] = fg["unit_debt_repayment"] * prod / 1e6 - debt_legacy
    out["debt_legacy"] = debt_legacy
    out["carbon"] = fg["unit_carbon_cost"] * prod / 1e6
    out["subsidy"] = fg["unit_subsidy_total"].fillna(0.0) * prod / 1e6
    out["unit_cost_check"] = fg["unit_production_cost"] * prod / 1e6
    out["capex"] = fg["capex_tcap"] * fg["capacity"] / 1e6

    keys = ["year", "geo_key", "product", "technology", "reductant"]
    agg = out.groupby(keys, as_index=False).sum()
    value_cols = [c for c in agg.columns if c not in keys]
    agg[value_cols] = agg[value_cols].round(4)
    inferred = (fg["capex_source"] == "inferred").mean()
    legacy_rows = fg["legacy_pay_tcap"] > 0
    legacy_share = fg.loc[legacy_rows, "production"].sum() / prod.sum()
    bad = fg["legacy_bad"].sum() / max(1, int(legacy_rows.sum()))
    logger.info(
        f"capex inferred for {inferred:.1%} of furnace-group-years, legacy debt on "
        f"{legacy_share:.2%} of production (negative residuals {bad:.1%}) "
        f"(lifetime {lifetime}, equity {equity_share:.0%}, {master.name})"
    )
    return agg


FIXED = "__fixed__"  # frame column marking rows that hold one value rather than a series


def h2_assumptions_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Tidy the hydrogen assumptions sheet: its metric rows (those marked
    "Fixed" in the second year hold one value), plus the block of Chinese
    province capex discounts (code in the Metric column, share in the Unit
    column) as fixed rows of one metric keyed by province."""
    years = sorted(c for c in df.columns if isinstance(c, (int, float)))
    main = df[df["Unit"].map(lambda u: isinstance(u, str)) & ~df["Metric"].astype(str).str.contains("provinces")].copy()
    main[FIXED] = main[years[1]].astype(str).str.strip().eq("Fixed")
    prov = df[df["Metric"].astype(str).str.startswith("CHN:")]
    rows = pd.DataFrame(
        {
            "Metric": "Chinese provinces capex discount",
            "Unit": prov["Metric"].values,
            years[0]: prov["Unit"].astype(float).values,
            FIXED: True,
        }
    )
    return pd.concat([main, rows], ignore_index=True)


def grid_scenario_name(projection: pd.Series) -> pd.Series:
    """The run-config name of each projection_scenario value of the Power grid emissivity sheet.

    Args:
        projection: The sheet's projection_scenario column.

    Returns:
        The names as ``chosen_grid_emissions_scenario`` holds them, by the excel reader's rule:
        ``projection_`` stripped, underscores to spaces, title case.
    """
    return projection.str.removeprefix("projection_").str.replace("_", " ").str.title()


# Per topic: frame tidies the sheet, select keeps the run's rows, keys and values (or years) pick columns
TOPICS: list[dict[str, Any]] = [
    {
        "id": "carbon",
        "label": "Carbon costs",
        "sheet": "Carbon cost",
        "unit": "USD/tCO2",
        "frame": lambda df: df[df["Default carbon cost - instrument name"] != "User-specified"],
        "keys": ["Country/Region_Bloc", "ISO 3-letter code_Bloc", "Default carbon cost - instrument name"],
        "key_labels": ["Country / bloc", "ISO3", "Instrument"],
        "years": True,
        "note": "Carbon price per tonne of CO2 by country or trade bloc with a carbon pricing instrument, from the "
        "master's Carbon cost sheet (rows priced by user specification are left out).",
    },
    {
        "id": "demand",
        "label": "Steel demand",
        "sheet": DEMAND_SHEET,
        "unit": "kt",
        "select": lambda df, run: df[
            (df["Metric"].str.strip() == "Crude steel consumption for forming [kt]")
            & (df["Scenario"].str.strip() == run["demand"])
        ],
        "keys": ["Country", "ISO-3 code"],
        "key_labels": ["Country", "ISO3"],
        "years": True,
        "note": "Crude steel consumption for forming, from the Demand and scrap availability sheet "
        "(the rows of the scenario each run was launched with).",
    },
    {
        "id": "scrap",
        "label": "Scrap supply",
        "sheet": DEMAND_SHEET,
        "unit": "kt",
        "select": lambda df, run: df[
            (df["Metric"].str.strip() == "Total available scrap") & (df["Scenario"].str.strip() == run["scrap"])
        ],
        "keys": ["Country", "ISO-3 code"],
        "key_labels": ["Country", "ISO3"],
        "years": True,
        "note": "Total available scrap, from the Demand and scrap availability sheet "
        "(the rows of the scenario each run was launched with).",
    },
    {
        "id": "grid",
        "label": "Power grid emissivity",
        "sheet": "Power grid emissivity",
        "unit": "gCO2/kWh",
        "select": lambda df, run: df[
            (df["projection_scenario"] == "historical") | (grid_scenario_name(df["projection_scenario"]) == run["grid"])
        ],
        "keys": ["country", "country_iso3", "region", "net_zero_target_year"],
        "key_labels": ["Country", "Geo key", "Region", "Net zero by"],
        "years": True,
        "pivot": {"year": "year", "value": "ghg_factor_scope_2"},
        "note": "Scope-2 emission factor of grid electricity (sheet unit tCO2/kWh shown as gCO2/kWh), from the "
        "Power grid emissivity sheet: the projection each run was launched with.",
    },
    {
        "id": "capital",
        "label": "Cost of capital",
        "sheet": "Cost of capital",
        "unit": "%",
        "keys": ["Country", "ISO-3 Code", "region_for_outputs", "Tech"],
        "key_labels": ["Country", "ISO3", "Region", "Technology"],
        "values": ["Cost of debt", "Cost of equity", "Cost of capital"],
        "value_labels": ["Cost of debt (%)", "Cost of equity (%)", "Cost of capital (%)"],
        "scale": 100,
        "note": "Cost of debt, equity and capital per country and technology, from the Cost of capital sheet.",
    },
    {
        "id": "tariffs",
        "label": "Tariffs",
        "sheet": "Tariffs",
        "unit": "",
        "keys": [
            "Tariff scenario name",
            "From ISO3",
            "To ISO3",
            "Commodity",
            "Metric (Volume/Emissions)",
            "Start year",
            "End year",
        ],
        "key_labels": ["Scenario", "From", "To", "Commodity", "Metric", "Start", "End"],
        "values": ["Tax [$/t]", "Tax [%]", "Quota [t]"],
        "note": "Import tariffs and quotas between countries or trade blocs, from the Tariffs sheet.",
    },
    {
        "id": "input_costs",
        "label": "Input costs",
        "sheet": "Input costs",
        "unit": "",
        "keys": ["ISO-3 code", "Commodity", "Unit"],
        "key_labels": ["ISO3", "Commodity", "Unit"],
        "years": True,
        "note": "Delivered cost of each energy carrier, reductant and flux per country (unit per row), from the Input costs sheet.",
    },
    {
        "id": "fopex",
        "label": "Fixed OPEX",
        "sheet": "Fixed OPEX",
        "unit": "USD/t",
        "keys": ["Country", "ISO 3-letter code"],
        "key_labels": ["Country", "ISO3"],
        "values": None,
        "note": "Fixed operating cost per tonne of capacity by country and technology, from the Fixed OPEX sheet.",
    },
    {
        "id": "greenfield_capex",
        "label": "Greenfield capex",
        "sheet": "Techno-economic details",
        "unit": "",
        "select": lambda df, run: df[df["Metric"] == "Greenfield capex"],
        "keys": ["Technology", "Product", "Unit"],
        "key_labels": ["Technology", "Product", "Unit"],
        "values": ["Value"],
        "value_labels": ["Greenfield capex"],
        "note": "Greenfield capex per tonne of annual capacity by technology (unit per row), from the Techno-economic details sheet.",
    },
    {
        "id": "renovation_capex",
        "label": "Renovation capex",
        "sheet": "Techno-economic details",
        "unit": "% of greenfield capex",
        "select": lambda df, run: df[df["Metric"] == "Renovation capex"],
        "keys": ["Technology", "Product"],
        "key_labels": ["Technology", "Product"],
        "values": ["Value"],
        "value_labels": ["Renovation capex (% of greenfield)"],
        "scale": 100,
        "note": "Renovation capex as a share of greenfield capex by technology, from the Techno-economic details sheet.",
    },
    {
        "id": "h2_assumptions",
        "label": "Hydrogen assumptions",
        "sheet": "Hydrogen_CAPEX_OPEX_Assumptions",
        "unit": "",
        "frame": h2_assumptions_frame,
        "keys": ["Metric", "Unit"],
        "key_labels": ["Metric", "Unit / province"],
        "years": True,
        "note": "Electrolyser efficiency and capex over time, with the fixed assumptions (capacity, utilisation, opex, "
        "lifetime, Chinese province capex discounts) listed apart, from the Hydrogen_CAPEX_OPEX_Assumptions sheet.",
    },
]
SHEETS = sorted({t["sheet"] for t in TOPICS})


def read_master(master: Path, cache: dict) -> dict:
    """The TOPICS sheets of one master workbook.

    Args:
        master: The master Excel the run was prepared from.
        cache: Reuse dict keyed by master path; the workbook is slow to read.

    Returns:
        ``{sheet name: frame}`` for every sheet in SHEETS.
    """
    if master not in cache:
        cache[master] = pd.read_excel(master, sheet_name=SHEETS)
        logger.info(f"read {master.name}")
    return cache[master]


def cell(v: Any) -> Any:
    """JSON-safe cell: NaN -> None, whole floats -> int, others rounded to 6 significant digits."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if float(v).is_integer():
            return int(v)
        return float(f"{v:.6g}")
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    return str(v)


def topic_rows(topic: dict, sheet: pd.DataFrame, run: dict) -> tuple[dict, list, set]:
    """Row key -> value tuple for one run, the value columns in tuple order,
    and the keys of fixed rows (one value each, not a series).

    Duplicate keys get an occurrence suffix. Year topics take every year
    column of the sheet (a long sheet with a `year` column is pivoted first).
    """
    df = topic["frame"](sheet) if "frame" in topic else sheet
    df = topic["select"](df, run) if "select" in topic else df
    df = df.dropna(subset=topic["keys"][:2], how="all")
    if topic.get("years"):
        if "pivot" in topic:
            df = df.pivot_table(
                index=topic["keys"], columns=topic["pivot"]["year"], values=topic["pivot"]["value"], aggfunc="first"
            ).reset_index()
        value_cols = sorted(c for c in df.columns if isinstance(c, (int, float)) and float(c).is_integer())
        scale = 1e6 if topic["id"] == "grid" else 1
    else:
        value_cols = topic["values"] or [c for c in df.columns if c not in topic["keys"] and c != "Day of update"]
        scale = topic.get("scale", 1)
    out: dict = {}
    seen: dict = {}
    fixed: set = set()
    for _, r in df.iterrows():
        key = tuple(cell(r[k]) for k in topic["keys"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = key + (seen[key],)
        values = tuple(
            cell(r[c] * scale if isinstance(r[c], (int, float)) and not isinstance(r[c], bool) else r[c])
            for c in value_cols
        )
        if FIXED in df.columns and r[FIXED]:
            values = (next((v for v in values if v is not None), None),)
            fixed.add(key)
        out[key] = values
    return out, [str(int(c)) if topic.get("years") else c for c in value_cols], fixed


def extract_inputs(master: Path, scenarios: dict[str, str]) -> dict:
    """One run's View data rows per topic, undiffed.

    Args:
        master: The master Excel the run was prepared from.
        scenarios: The run's ``demand``, ``scrap`` and ``grid`` scenario names.

    Returns:
        ``{"schema", "scenarios", "topics"}``: per topic its id, label, sheet,
        note, unit, ``key_columns``, ``value_columns`` and ``rows``, one row
        ``{"k": [...], "vals": [...]}`` per :func:`topic_rows` key, with
        ``"fixed": true`` on fixed rows.

    Notes:
        A duplicate key keeps its occurrence number as an extra last element of
        ``k``, so the diff across runs pairs duplicate rows by occurrence and
        trims ``k`` to the key columns for display.
    """
    sheets = read_master(master, {})
    topics = []
    for topic in TOPICS:
        rows, value_columns, fixed = topic_rows(topic, sheets[topic["sheet"]], scenarios)
        topics.append(
            {
                "id": topic["id"],
                "label": topic["label"],
                "sheet": topic["sheet"],
                "note": topic["note"],
                "unit": topic["unit"],
                "key_columns": topic["key_labels"],
                "value_columns": topic.get("value_labels", value_columns),
                "rows": [
                    {"k": list(key), "vals": list(vals), **({"fixed": True} if key in fixed else {})}
                    for key, vals in rows.items()
                ],
            }
        )
    return {"schema": SCHEMA_VERSION, "scenarios": scenarios, "topics": topics}


PAGE_ROWS = 5000
RESULTS_DIR = "results"
INDEX_NAME = "index.json"
# (folder id, folder label, source glob under the run folder)
RESULT_FOLDERS = [
    ("run", "Run", "post_processed_*.csv"),
    ("data", "Data", "data/*.csv"),
    ("lcoe", "LCOE", "data/LCOE/*.csv"),
    ("lcoh", "LCOH", "data/LCOH/*.csv"),
    ("trade", "Trade", "TM/*.csv"),
    ("policy", "Policy", "data/policy/*.csv"),
]
YEAR_FILE = re.compile(r"^(.+)_(\d{4})$")
STACKED_FILE = re.compile(r"^(.+)_\d{4}_\d{4}$")


def source_rows(paths: list[Path], merge: bool) -> tuple[list[str], Iterator[list[str]]]:
    """Header and rows of one output table; a yearly family is merged with a leading year column.

    Args:
        paths: Source CSVs of the table, sorted.
        merge: Whether the paths are a yearly family, each file one year.

    Returns:
        (header, row iterator).
    """

    def rows() -> Iterator[list[str]]:
        for p in paths:
            year = p.stem[-4:]
            with open(p, newline="") as f:
                reader = csv.reader(f)
                next(reader)
                for row in reader:
                    yield [year, *row] if merge else row

    with open(paths[0], newline="") as f:
        header = next(csv.reader(f))
    return (["year", *header] if merge else header), rows()


def write_table(stem: str, paths: list[Path], merge: bool, folder_dir: Path) -> dict:
    """Write <stem>.csv.gz and its JSON chunks; return the table's index entry.

    Args:
        stem: Output stem of the table.
        paths: Source CSVs of the table, sorted.
        merge: Whether the paths are a yearly family, merged with a leading year column.
        folder_dir: The results folder the table goes into.

    Returns:
        The table's entry in results/index.json.
    """
    header, rows = source_rows(paths, merge)
    pages_dir = folder_dir / f"{stem}.pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    n_rows, n_chunks = 0, 0
    chunk: list[list[str]] = []

    def flush() -> None:
        nonlocal chunk, n_chunks
        with gzip.open(pages_dir / f"{n_chunks}.json.gz", "wt", compresslevel=6) as g:
            json.dump(chunk, g, separators=(",", ":"))
        n_chunks += 1
        chunk = []

    out = folder_dir / f"{stem}.csv.gz"
    with gzip.open(out, "wt", newline="", compresslevel=6) as g:
        writer = csv.writer(g, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
            chunk.append(row)
            n_rows += 1
            if len(chunk) == PAGE_ROWS:
                flush()
    if chunk or not n_chunks:
        flush()
    return {
        "id": stem,
        "name": f"{stem}.csv",
        "file": f"{stem}.csv.gz",
        "pages": f"{stem}.pages",
        "columns": header,
        "rows": n_rows,
        "chunks": n_chunks,
        "chunk_rows": PAGE_ROWS,
        "bytes": out.stat().st_size,
    }


def result_tables(paths: list[Path]) -> list[tuple[str, list[Path], bool]]:
    """Group one folder's CSVs into the tables of the Download results pane.

    Args:
        paths: The CSVs one RESULT_FOLDERS glob matched.

    Returns:
        ``(stem, paths, merge)`` per table, by stem: a file whose stem ends in
        ``_<year>`` joins its yearly family, packed under the bare stem with
        ``merge`` true; every other file is a table of its own.

    Notes:
        A stacked ``<stem>_<y1>_<y2>.csv`` holds its yearly family's rows
        already, so a family with a stacked twin beside it is left out.
    """
    stacked = {m.group(1) for p in paths if (m := STACKED_FILE.match(p.stem))}
    families: dict[str, list[Path]] = {}
    tables = []
    for p in sorted(paths):
        year_match = YEAR_FILE.match(p.stem)
        if year_match and not STACKED_FILE.match(p.stem):
            if year_match.group(1) not in stacked:
                families.setdefault(year_match.group(1), []).append(p)
        else:
            tables.append((p.stem, [p], False))
    tables += [(stem, family, True) for stem, family in families.items()]
    return sorted(tables)


def extract_results(run_dir: Path, results_dir: Path) -> None:
    """Write one run's results folder: every table the RESULT_FOLDERS globs find, then index.json.

    Args:
        run_dir: The run folder.
        results_dir: The folder to write, ``<run>/viewer/results``.

    Notes:
        A glob with no match leaves its folder out with a warning: which tables
        exist depends on the run's settings and the model version.
    """
    folders: list[dict[str, Any]] = []
    for folder_id, label, pattern in RESULT_FOLDERS:
        paths = sorted(run_dir.glob(pattern))
        if not paths:
            logger.warning(f"no {pattern} in {run_dir}, skipping the {label} folder of the results")
            continue
        entries = [
            write_table(stem, files, merge, results_dir / folder_id) for stem, files, merge in result_tables(paths)
        ]
        folders.append({"id": folder_id, "label": label, "files": entries})
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / INDEX_NAME).write_text(json.dumps({"folders": folders}, separators=(",", ":")) + "\n")
    total = sum(f["bytes"] for d in folders for f in d["files"])
    logger.info(f"{sum(len(d['files']) for d in folders)} result tables, {total / 1e6:.1f} MB gz")


def utc_time(seconds: float) -> str:
    """A POSIX time in ISO 8601 form, in UTC with its offset, to the second.

    Args:
        seconds: Seconds since the epoch, such as a file's ``st_mtime``.

    Returns:
        ``YYYY-MM-DDTHH:MM:SS+00:00``.

    Notes:
        Not the process's local time: the app's worker runs in Django's ``TIME_ZONE`` (UTC), the CLI in the machine's.
        One offset and a fixed width also keep text order in time order, which the catalogue sorts runs by.
    """
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="seconds")


def write_csv(frame: pd.DataFrame | None, viewer_dir: Path, name: str) -> None:
    """Write one viewer CSV, or warn that its source page is missing.

    Args:
        frame: The extracted rows, None when the plotter skipped the page they come from.
        viewer_dir: The run's viewer folder.
        name: The CSV file name.
    """
    if frame is None:
        logger.warning(f"no source page, skipping {name}")
        return
    out = viewer_dir / name
    frame.to_csv(out, index=False)
    logger.info(f"{len(frame)} rows -> {name}, {out.stat().st_size // 1024} KB")


def write_viewer_files(run_dir: Path, master_excel: Path | None = None) -> None:
    """Write ``<run>/viewer/`` from the run folder's files and the run's master workbook.

    Args:
        run_dir: The run folder (the simulation's output directory).
        master_excel: The master workbook to read; None reads the path recorded in
            ``preparation_metadata.json``.

    Raises:
        FileNotFoundError: If the post-processed CSV, either JSON file or the master is missing, or the
            metadata records no master and none is given.

    Notes:
        An existing ``viewer/`` folder is removed first and ``run.json`` is written last, so a
        folder holding ``run.json`` is complete. A plot page the plotter skipped skips the file
        lifted from it with a warning.
    """
    viewer_dir = run_dir / VIEWER_DIR
    if viewer_dir.exists():
        shutil.rmtree(viewer_dir)
    config_path = run_dir / "simulation_config.json"
    config = json.loads(config_path.read_text())
    metadata = json.loads((run_dir / "preparation_metadata.json").read_text())
    recorded = metadata["master_excel"]
    if master_excel is None and recorded is None:
        raise FileNotFoundError(f"{run_dir / 'preparation_metadata.json'} records no master workbook")
    master = master_excel or Path(recorded)
    if not master.exists():
        raise FileNotFoundError(f"master workbook {master} not found")
    source_csvs = sorted(run_dir.glob("post_processed_*.csv"))
    if not source_csvs:
        raise FileNotFoundError(f"no post_processed_*.csv in {run_dir}")
    source_csv = source_csvs[-1]
    pages_dir = run_dir / PAGES_DIR
    viewer_dir.mkdir()

    write_csv(extract_timeseries(source_csv), viewer_dir, TIMESERIES_NAME)
    write_csv(extract_greenfield(source_csv), viewer_dir, GREENFIELD_NAME)
    write_csv(extract_annual_costs(source_csv, master, config), viewer_dir, ANNUAL_COSTS_NAME)
    write_csv(extract_charge(pages_dir), viewer_dir, CHARGE_NAME)
    write_csv(extract_reductant(pages_dir), viewer_dir, REDUCTANT_NAME)
    write_csv(extract_trade(pages_dir), viewer_dir, TRADE_NAME)
    demand = steel_demand_table(master, config["chosen_demand_scenario"], {})
    write_csv(extract_supply(pages_dir, demand), viewer_dir, SUPPLY_NAME)

    geo = extract_geo(pages_dir)
    if geo is None:
        logger.warning(f"no emissions.html, skipping {GEO_NAME}")
    else:
        (viewer_dir / GEO_NAME).write_text(json.dumps(geo, separators=(",", ":")) + "\n")
    scenarios = {
        "demand": config["chosen_demand_scenario"],
        "scrap": config["chosen_scrap_scenario"],
        "grid": config["chosen_grid_emissions_scenario"],
    }
    inputs = extract_inputs(master, scenarios)
    (viewer_dir / INPUTS_NAME).write_text(json.dumps(inputs, separators=(",", ":")) + "\n")
    extract_results(run_dir, viewer_dir / RESULTS_DIR)

    # Keys a CLI run or a run from before this module lacks in preparation_metadata.json stay null
    run = {
        "schema": SCHEMA_VERSION,
        "name": config["run_name"] or run_dir.name,
        "folder": run_dir.name,
        "made_in": metadata.get("made_in"),
        "started": utc_time(config_path.stat().st_mtime),
        "ended": utc_time(datetime.now().timestamp()),
        "steelo_version": importlib.metadata.version("steel-model"),
        "input_data": {
            "master": Path(recorded or master).name,
            "master_source": metadata.get("master_source"),
            "master_name": metadata.get("master_name"),
            "preparation_name": metadata.get("preparation_name"),
        },
        "pages": sorted(p.name for p in pages_dir.glob("*.html")),
        "files": sorted(p.name for p in viewer_dir.iterdir()),
    }
    (viewer_dir / RUN_NAME).write_text(json.dumps(run, indent=2) + "\n")
    logger.info(f"viewer files written to {viewer_dir}")


def missing_inputs(run_dir: Path, master_excel: Path | None = None) -> list[Path]:
    """The files :func:`write_viewer_files` cannot do without that one run folder lacks.

    Args:
        run_dir: The run folder.
        master_excel: The master workbook to read instead of the recorded one, or None.

    Returns:
        The missing paths: the post-processed CSV (as its glob), ``simulation_config.json``,
        ``preparation_metadata.json``, the master (``preparation_metadata.json:master_excel`` when
        the metadata records none) and ``emissions.html``, which ``geo.json`` needs. Empty when the
        run has every one.

    Notes:
        The recorded master is checked only when ``preparation_metadata.json`` exists. The other
        plot pages are optional: a missing one skips its file with a warning, as at run end.
    """
    metadata_path = run_dir / "preparation_metadata.json"
    missing = [] if any(run_dir.glob("post_processed_*.csv")) else [run_dir / "post_processed_*.csv"]
    missing += [p for p in (run_dir / "simulation_config.json", metadata_path) if not p.exists()]
    if master_excel is None and metadata_path.exists():
        recorded = json.loads(metadata_path.read_text())["master_excel"]
        # The app records null for a run without a ready data preparation
        if recorded is None:
            missing.append(run_dir / "preparation_metadata.json:master_excel")
        else:
            master_excel = Path(recorded)
    if master_excel is not None and not master_excel.exists():
        missing.append(master_excel)
    emissions_page = run_dir / PAGES_DIR / "emissions.html"
    if not emissions_page.exists():
        missing.append(emissions_page)
    return missing


def regenerate(argv: list[str]) -> int:
    """Rebuild ``<run>/viewer/`` for finished runs, leaving the plot pages and model outputs alone.

    Args:
        argv: Command-line arguments: one or more run folders and an optional ``--master-excel``.

    Returns:
        0 when every run's viewer folder was written, 1 when a run lacks a required input; then
        nothing is written.
    """
    parser = argparse.ArgumentParser(
        prog="steelo-viewer regenerate", description="Rebuild the viewer files of finished runs."
    )
    parser.add_argument("run_dirs", nargs="+", type=Path, metavar="RUN_DIR", help="run folder to rebuild")
    parser.add_argument(
        "--master-excel", type=Path, help="master workbook to read instead of the one each run recorded"
    )
    args = parser.parse_args(argv)

    missing = [path for run_dir in args.run_dirs for path in missing_inputs(run_dir, args.master_excel)]
    for path in missing:
        logger.error(f"missing input: {path}")
    if missing:
        return 1
    for run_dir in args.run_dirs:
        write_viewer_files(run_dir, args.master_excel)
    return 0
