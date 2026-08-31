"""Row packing for the trade-matrix viewer (``trade_matrix.html``).

The viewer shows, for one year, how much steel, iron, iron ore or scrap each
geography shipped to each other geography, from the trade model's per-year
allocation files (``TM/steel_trade_allocations_<year>.csv``). Steel rows run
plant → demand centre, iron rows (pig iron, HBI and DRI by grade, and the
on-site hot metal) iron plant → steelmaking furnace group, ore rows mine →
furnace group, and scrap rows per-country scrap supplier → furnace group.
Steel, iron and scrap origins and destinations are countries (plants carry a
sub-national geo_unit but demand centres do not, so a finer diagonal is not
definable); ore origins are the mine sheet's own region labels, as mines carry
that label rather than a resolved ISO3.
"""

import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

ALLOCATIONS_PATTERN = re.compile(r"^steel_trade_allocations_(\d{4})\.csv$")
# Traded commodity → product of the viewer.
COMMODITY_PRODUCTS = {
    "steel": "steel",
    "scrap": "scrap",
    "pig_iron": "iron",
    "hbi_high": "iron",
    "hbi_mid": "iron",
    "hbi_low": "iron",
    "dri_high": "iron",
    "dri_mid": "iron",
    "dri_low": "iron",
    "hot_metal": "iron",
    "liquid_iron": "iron",
    "electrolytic_iron": "iron",
    "io_high": "ore",
    "io_mid": "ore",
    "io_low": "ore",
}
# Allocated commodities that are deliberately not metal trade (reductants, captured CO2).
# Anything in neither set is dropped with a warning, so a new commodity surfaces loudly.
EXCLUDED_COMMODITIES = {"bio_pci", "co2_stored"}
# Products whose origin is the location's country label rather than its ISO3.
LABELLED_ORIGINS = {"ore"}
COLUMNS = ["commodity", "source_location", "source_tech", "destination_location", "allocated_volume"]
# The location columns hold Location reprs; the fields are read off them.
ISO3_PATTERN = re.compile(r"\biso3='([A-Z]{3})'")
COUNTRY_PATTERN = re.compile(r"\bcountry='([^']*)'")
FLOW_KEYS = ["year", "product", "commodity", "origin", "destination", "technology"]


def allocation_files(tm_dir: Path) -> dict[int, Path]:
    """The run's per-year allocation files, keyed by year.

    Args:
        tm_dir: The run's ``TM`` output directory.

    Returns:
        ``{year: path}`` for every ``steel_trade_allocations_<year>.csv`` found, in year
        order; empty when the directory does not exist or holds none.
    """
    if not tm_dir.is_dir():
        return {}
    files: dict[int, Path] = {}
    for path in tm_dir.iterdir():
        match = ALLOCATIONS_PATTERN.match(path.name)
        if match:
            files[int(match.group(1))] = path
    return dict(sorted(files.items()))


def iso3_of(location: str) -> str:
    """The ISO3 code of a Location repr.

    Args:
        location: A ``Location(...)`` repr as written to the allocation files.

    Returns:
        The three-letter code of its ``iso3`` field.

    Raises:
        ValueError: If the repr carries no ISO3.
    """
    match = ISO3_PATTERN.search(location)
    if match is None:
        raise ValueError(f"No iso3 in location {location!r}")
    return match.group(1)


def country_label_of(location: str) -> str:
    """The country label of a Location repr — for mines, the mine sheet's region name.

    Args:
        location: A ``Location(...)`` repr as written to the allocation files.

    Returns:
        Its non-empty ``country`` field.

    Raises:
        ValueError: If the repr carries no country label.
    """
    match = COUNTRY_PATTERN.search(location)
    if match is None or not match.group(1):
        raise ValueError(f"No country label in location {location!r}")
    return match.group(1)


def read_flows(files: dict[int, Path]) -> pd.DataFrame:
    """Flows per year, commodity, origin, destination country and technology.

    Args:
        files: Output of :func:`allocation_files`.

    Returns:
        Columns ``year, product, commodity, origin, destination, technology, volume_mt``:
        the allocated tonnes of each commodity in :data:`COMMODITY_PRODUCTS` summed over
        the sources of the origin and the receivers of the destination country. Origins
        are ISO3 codes, except for :data:`LABELLED_ORIGINS` where they are the source's
        country label. A year whose file holds no such allocations (a failed trade LP
        writes a header-only file) contributes no rows. Commodities in neither
        :data:`COMMODITY_PRODUCTS` nor :data:`EXCLUDED_COMMODITIES` are dropped with
        a warning naming them.

    Raises:
        ValueError: If a file lacks the allocation columns or a location lacks the
        needed field.
    """
    frames = []
    unknown: set[str] = set()
    for year, path in files.items():
        # Suppliers write the literal technology "N/A", which pandas would otherwise read as NaN
        # and the grouping would then drop.
        table = pd.read_csv(path, usecols=COLUMNS, keep_default_na=False)
        unknown |= set(table["commodity"]) - COMMODITY_PRODUCTS.keys() - EXCLUDED_COMMODITIES
        metal = table[table["commodity"].isin(COMMODITY_PRODUCTS)]
        if metal.empty:
            continue
        product = metal["commodity"].map(COMMODITY_PRODUCTS)
        labelled = product.isin(LABELLED_ORIGINS)
        origin = pd.concat(
            [
                metal.loc[labelled, "source_location"].map(country_label_of),
                metal.loc[~labelled, "source_location"].map(iso3_of),
            ]
        )
        flows = pd.DataFrame(
            {
                "year": year,
                "product": product,
                "commodity": metal["commodity"],
                "origin": origin,
                "destination": metal["destination_location"].map(iso3_of),
                "technology": metal["source_tech"],
                "volume_mt": metal["allocated_volume"] / 1e6,
            }
        )
        frames.append(flows.groupby(FLOW_KEYS, as_index=False)["volume_mt"].sum())
    if unknown:
        logger.warning(
            "Commodities missing from the trade viewers: %s — map them in COMMODITY_PRODUCTS "
            "or add them to EXCLUDED_COMMODITIES",
            ", ".join(sorted(unknown)),
        )
    if not frames:
        return pd.DataFrame(columns=FLOW_KEYS + ["volume_mt"])
    return pd.concat(frames, ignore_index=True)


def pack_rows(flows: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact flows for embedding in the viewer.

    Args:
        flows: Output of :func:`read_flows`.

    Returns:
        One short-keyed record per flow: ``y`` year, ``p`` product, ``c`` commodity, ``o``
        origin, ``d`` destination, ``t`` technology and ``v`` volume (Mt, four decimals).
        Flows that round to zero are dropped.
    """
    rows = []
    for row in flows.to_dict("records"):
        volume = round(float(row["volume_mt"]), 4)
        if volume > 0:
            rows.append(
                {
                    "y": int(row["year"]),
                    "p": row["product"],
                    "c": row["commodity"],
                    "o": row["origin"],
                    "d": row["destination"],
                    "t": row["technology"],
                    "v": volume,
                }
            )
    return rows
