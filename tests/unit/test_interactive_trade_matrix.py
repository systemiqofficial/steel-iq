"""Tests for the trade-matrix viewer's flow packing (steelo.utilities.interactive.trade_matrix)."""

import csv
from pathlib import Path

import pytest

from steelo.utilities.interactive import trade_matrix

HEADER = [
    "commodity",
    "source_type",
    "source_id",
    "source_location",
    "capacity_at_source",
    "source_tech",
    "destination_type",
    "destination_id",
    "destination_location",
    "allocated_volume",
    "allocation_cost",
    "demand_at_destination",
    "supply_at_source",
]


def location(iso3: str, geo_unit: str | None = None) -> str:
    """A Location repr as the allocation export writes it."""
    unit = f"'{geo_unit}'" if geo_unit else "None"
    return (
        f"Location(lat=1.0, lon=2.0, country='{iso3}', region='Somewhere', iso3='{iso3}', "
        f"distance_to_other_iso3=None, geo_unit={unit})"
    )


def allocation(commodity: str, source: str, tech: str, destination: str, volume: float) -> list:
    return [
        commodity,
        "Plant-FurnaceGroup",
        "P1_0",
        source,
        1e6,
        tech,
        "DemandCenter",
        "DC",
        destination,
        volume,
        0,
        0,
        0,
    ]


def write_allocations(tm_dir: Path, year: int, rows: list[list]) -> Path:
    """Write ``steel_trade_allocations_<year>.csv`` with the export's full column set."""
    tm_dir.mkdir(parents=True, exist_ok=True)
    path = tm_dir / f"steel_trade_allocations_{year}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def sample_tm_dir(tmp_path: Path) -> Path:
    """2025 with steel, iron and feedstock allocations; 2026 header-only, as a failed trade LP leaves it."""
    tm_dir = tmp_path / "TM"
    write_allocations(
        tm_dir,
        2025,
        [
            # Two Chinese furnace groups serving China: summed into one CHN → CHN BOF flow
            allocation("steel", location("CHN", "CN-HE"), "BOF", location("CHN"), 300_000_000.0),
            allocation("steel", location("CHN", "CN-SD"), "BOF", location("CHN"), 100_000_000.0),
            allocation("steel", location("CHN", "CN-SD"), "EAF", location("IND"), 25_000_000.0),
            allocation("steel", location("DEU"), "EAF", location("DEU"), 10_000_000.0),
            allocation("steel", location("DEU"), "EAF", location("IND"), 20.0),
            # Iron products go plant → steelmaking furnace group
            allocation("pig_iron", location("CHN", "CN-HE"), "BF", location("JPN"), 5_000_000.0),
            allocation("hot_metal", location("CHN", "CN-HE"), "BF", location("CHN"), 200_000_000.0),
            # Feedstock allocations are not metal trade
            allocation("scrap", location("DEU"), "N/A", location("DEU"), 50_000_000.0),
            allocation("io_high", location("AUS"), "N/A", location("CHN"), 80_000_000.0),
        ],
    )
    write_allocations(tm_dir, 2026, [])
    (tm_dir / "steel_trade_allocations_2025.pkl").write_bytes(b"")
    return tm_dir


def test_allocation_files_keyed_by_year(tmp_path: Path) -> None:
    """Only the per-year CSVs count, in year order; a missing directory yields nothing."""
    tm_dir = sample_tm_dir(tmp_path)

    files = trade_matrix.allocation_files(tm_dir)

    assert list(files) == [2025, 2026]
    assert files[2025] == tm_dir / "steel_trade_allocations_2025.csv"
    assert trade_matrix.allocation_files(tmp_path / "absent") == {}


def test_read_flows_sums_metal_per_commodity_country_pair_and_technology(tmp_path: Path) -> None:
    """Plant-level allocations collapse to commodity × origin × destination × technology in Mt; feedstocks are ignored."""
    flows = trade_matrix.read_flows(trade_matrix.allocation_files(sample_tm_dir(tmp_path)))

    assert list(flows.columns) == ["year", "product", "commodity", "origin", "destination", "technology", "volume_mt"]
    assert set(flows["year"]) == {2025}
    by_key = {(r.product, r.commodity, r.origin, r.destination, r.technology): r.volume_mt for r in flows.itertuples()}
    assert by_key[("steel", "steel", "CHN", "CHN", "BOF")] == pytest.approx(400.0)
    assert by_key[("steel", "steel", "CHN", "IND", "EAF")] == pytest.approx(25.0)
    assert by_key[("steel", "steel", "DEU", "DEU", "EAF")] == pytest.approx(10.0)
    assert by_key[("iron", "pig_iron", "CHN", "JPN", "BF")] == pytest.approx(5.0)
    assert by_key[("iron", "hot_metal", "CHN", "CHN", "BF")] == pytest.approx(200.0)
    assert len(by_key) == 6


def test_read_flows_rejects_location_without_iso3(tmp_path: Path) -> None:
    """A location repr that carries no ISO3 fails loudly rather than becoming an unnamed geography."""
    tm_dir = tmp_path / "TM"
    write_allocations(tm_dir, 2025, [allocation("steel", "Location(lat=1.0, lon=2.0)", "BOF", location("CHN"), 1.0)])

    with pytest.raises(ValueError, match="No iso3"):
        trade_matrix.read_flows(trade_matrix.allocation_files(tm_dir))


def test_pack_rows_compacts_flows_and_drops_rounded_zeros(tmp_path: Path) -> None:
    """Rows carry short keys with Mt to four decimals; the 20 t DEU → IND flow rounds away."""
    flows = trade_matrix.read_flows(trade_matrix.allocation_files(sample_tm_dir(tmp_path)))

    packed = trade_matrix.pack_rows(flows)

    assert {"y": 2025, "p": "steel", "c": "steel", "o": "CHN", "d": "IND", "t": "EAF", "v": 25.0} in packed
    assert {"y": 2025, "p": "iron", "c": "pig_iron", "o": "CHN", "d": "JPN", "t": "BF", "v": 5.0} in packed
    assert not any(row["o"] == "DEU" and row["d"] == "IND" for row in packed)
    assert len(packed) == 5
