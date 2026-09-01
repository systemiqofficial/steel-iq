"""Row packing for the supply and demand viewer (``supply_demand.html``).

The viewer shows, per year, how much of a constrained commodity was used
against how much was available: steel demand against deliveries per
demand-centre country, scrap and iron ore drawn against supplier capacity
per source country, and CO2 storage and biomass (bio-PCI) use per consuming
country against the input-side limits. Usage (and steel demand) comes from
the trade LP's per-year allocation files; the other availabilities come from
the prepared inputs (``fixtures/suppliers.json`` and
``fixtures/biomass_availability.json``). A biomass budget belongs to a whole
TIAM-UCL region rather than one country, so those rows carry a ``region:``
geography; the viewer counts a budget in full once any member country is
selected, noting how many are only partly covered.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from steelo.domain.models import BiomassAvailability, CountryMapping, Supplier

from .trade_matrix import country_label_of, iso3_of

logger = logging.getLogger(__name__)

# Commodities whose use is counted at the source (who supplied) and their viewer group.
SOURCE_GROUPS = {"scrap": "scrap", "io_low": "ore", "io_mid": "ore", "io_high": "ore"}
# Commodities whose use is counted at the destination (who consumed): the LP feeds them
# from one virtual global supplier, so the source carries no geography.
DESTINATION_GROUPS = {"co2_stored": "co2", "bio_pci": "bio"}
USAGE_COLUMNS = [
    "commodity",
    "source_location",
    "destination_type",
    "destination_id",
    "destination_location",
    "allocated_volume",
    "demand_at_destination",
]
# Geography prefix of a shared regional budget (kept distinct from ISO3 codes,
# which some TIAM-UCL region labels could collide with).
REGION_PREFIX = "region:"


def geo_resolver(country_mappings: list[CountryMapping]) -> Callable[[str], str]:
    """A resolver from a source's country label to an ISO3 code.

    Args:
        country_mappings: The run's country mappings.

    Returns:
        A function mapping a label to its ISO3: an ISO3 code passes through, a country
        name (case-insensitive, ``&`` read as ``and``) resolves via the mappings, and
        an unresolved label is returned unchanged, so the viewer shows it as its own
        geography rather than dropping the volume.
    """

    def normalise(name: str) -> str:
        return name.strip().lower().replace("&", "and")

    iso3s = {mapping.iso3 for mapping in country_mappings}
    by_name = {normalise(mapping.country): mapping.iso3 for mapping in country_mappings}

    def resolve(label: str) -> str:
        if label in iso3s:
            return label
        return by_name.get(normalise(label), label)

    return resolve


def tiam_regions(country_mappings: list[CountryMapping]) -> dict[str, list[str]]:
    """TIAM-UCL regions and their member ISO3 codes, for the shared regional budgets.

    Args:
        country_mappings: The run's country mappings.

    Returns:
        ``{region_label: [iso3, ...]}`` for every non-empty region label, members sorted.
    """
    members: dict[str, list[str]] = {}
    for mapping in country_mappings:
        if mapping.tiam_ucl_region:
            members.setdefault(mapping.tiam_ucl_region, []).append(mapping.iso3)
    return {region: sorted(iso3s) for region, iso3s in sorted(members.items())}


def _source_geo(location: str, resolve: Callable[[str], str]) -> str:
    """The source's country: its ISO3 when the Location repr has one, else its resolved label."""
    try:
        return iso3_of(location)
    except ValueError:
        return resolve(country_label_of(location))


def read_usage(files: dict[int, Path], resolve: Callable[[str], str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Commodity use and steel demand per year and country, from the allocation files.

    Args:
        files: Output of :func:`~.trade_matrix.allocation_files`.
        resolve: Output of :func:`geo_resolver`, for sources without an ISO3 (ore mines).

    Returns:
        ``(used, steel_demand)``. ``used`` has columns ``year, group, geo, grade,
        volume_mt``: steel delivered per demand-centre country, scrap and ore drawn per
        source country, CO2 stored and bio-PCI burnt per consuming plant country; ore
        rows carry their commodity (``io_low/mid/high``) as the grade, all others "".
        ``steel_demand`` has columns ``year, geo, volume_mt``: the demand centres'
        demand (each centre counted once, however many plants supplied it).

    Raises:
        ValueError: If a file lacks the usage columns or a location lacks the needed field.
    """
    used_frames, demand_frames = [], []
    for year, path in files.items():
        table = pd.read_csv(path, usecols=USAGE_COLUMNS, keep_default_na=False)
        parts = []
        steel = table[(table["commodity"] == "steel") & (table["destination_type"] == "DemandCenter")]
        if not steel.empty:
            geo = steel["destination_location"].map(iso3_of)
            parts.append(
                pd.DataFrame({"group": "steel", "geo": geo, "grade": "", "volume_mt": steel["allocated_volume"] / 1e6})
            )
            centres = steel.assign(geo=geo).drop_duplicates("destination_id")
            demand = pd.to_numeric(centres["demand_at_destination"]) / 1e6
            demand_frames.append(
                pd.DataFrame({"year": year, "geo": centres["geo"], "volume_mt": demand})
                .groupby(["year", "geo"], as_index=False)["volume_mt"]
                .sum()
            )
        sourced = table[table["commodity"].isin(SOURCE_GROUPS)]
        if not sourced.empty:
            group = sourced["commodity"].map(SOURCE_GROUPS)
            parts.append(
                pd.DataFrame(
                    {
                        "group": group,
                        "geo": sourced["source_location"].map(lambda loc: _source_geo(loc, resolve)),
                        "grade": sourced["commodity"].where(group == "ore", ""),
                        "volume_mt": sourced["allocated_volume"] / 1e6,
                    }
                )
            )
        consumed = table[table["commodity"].isin(DESTINATION_GROUPS)]
        if not consumed.empty:
            parts.append(
                pd.DataFrame(
                    {
                        "group": consumed["commodity"].map(DESTINATION_GROUPS),
                        "geo": consumed["destination_location"].map(iso3_of),
                        "grade": "",
                        "volume_mt": consumed["allocated_volume"] / 1e6,
                    }
                )
            )
        if parts:
            year_used = pd.concat(parts).groupby(["group", "geo", "grade"], as_index=False)["volume_mt"].sum()
            year_used.insert(0, "year", year)
            used_frames.append(year_used)
    empty = pd.DataFrame(columns=["year", "group", "geo", "grade", "volume_mt"])
    used = pd.concat(used_frames, ignore_index=True) if used_frames else empty
    steel_demand = (
        pd.concat(demand_frames, ignore_index=True)
        if demand_frames
        else pd.DataFrame(columns=["year", "geo", "volume_mt"])
    )
    return used, steel_demand


def availability_rows(
    suppliers: Iterable[Supplier],
    biomass_items: Iterable[BiomassAvailability],
    country_mappings: list[CountryMapping],
    years: set[int],
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Available volumes per year, group and geography, from the prepared inputs.

    Args:
        suppliers: The prepared scrap and ore suppliers (``fixtures/suppliers.json``);
            other commodities' suppliers are ignored.
        biomass_items: The prepared biomass and CO2 storage rows
            (``fixtures/biomass_availability.json``), mapped to geographies as the
            constraint loader maps them: a ``co2`` metric with a country is that
            country's CO2 storage limit, a country name resolves to that country, a
            TIAM-UCL region label is a budget shared by the whole region, and a region
            label that is itself an ISO3 code or country name is that single country's.
        country_mappings: The run's country mappings.
        years: The years to keep (the run's allocation years).

    Returns:
        ``(avail, region_budgets)``. ``avail`` has columns ``year, group, geo, grade,
        volume_mt`` (grade as in :func:`read_usage`: the ore commodity, else "");
        a shared regional budget's geo is ``region:<label>``.
        ``region_budgets`` maps each such geo to its member ISO3 codes, so the viewer
        can tell which selections a budget belongs to.
    """
    resolve = geo_resolver(country_mappings)
    regions = tiam_regions(country_mappings)
    iso3s = {mapping.iso3 for mapping in country_mappings}

    rows = []
    for supplier in suppliers:
        group = SOURCE_GROUPS.get(supplier.commodity)
        if group is None:
            continue
        geo = supplier.location.iso3 or resolve(supplier.location.country)
        grade = supplier.commodity if group == "ore" else ""
        for year, capacity in supplier.capacity_by_year.items():
            if int(year) in years:
                rows.append((int(year), group, geo, grade, float(capacity) / 1e6))

    region_budgets: dict[str, list[str]] = {}
    for item in biomass_items:
        if int(item.year) not in years:
            continue
        group = "co2" if item.metric and "co2" in item.metric.lower() else "bio"
        if item.country and resolve(item.country) in iso3s:
            geo = resolve(item.country)
        elif item.region in regions:
            geo = REGION_PREFIX + item.region
            region_budgets[geo] = regions[item.region]
        elif resolve(item.region) in iso3s:
            geo = resolve(item.region)
        else:
            logger.warning(
                "Cannot place %s budget for region %r / country %r — dropped", group, item.region, item.country
            )
            continue
        rows.append((int(item.year), group, geo, "", item.availability / 1e6))

    avail = pd.DataFrame(rows, columns=["year", "group", "geo", "grade", "volume_mt"])
    return avail.groupby(["year", "group", "geo", "grade"], as_index=False)[["volume_mt"]].sum(), region_budgets


def pack_rows(table: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact usage or availability rows for embedding in the viewer.

    Args:
        table: Columns ``year, group, geo, grade, volume_mt`` (:func:`read_usage`
            output, or :func:`availability_rows` output with the steel demand appended).

    Returns:
        One short-keyed record per row: ``y`` year, ``c`` commodity group, ``g``
        geography, ``v`` volume (Mt, four decimals) and, on ore rows, ``s`` grade.
        Rows that round to zero are dropped.
    """
    rows = []
    for row in table.to_dict("records"):
        volume = round(float(row["volume_mt"]), 4)
        if volume > 0:
            record = {"y": int(row["year"]), "c": row["group"], "g": row["geo"], "v": volume}
            if row["grade"]:
                record["s"] = row["grade"]
            rows.append(record)
    return rows
