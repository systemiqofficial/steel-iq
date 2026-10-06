"""Tests for the greenfield map viewer's group packing (steelo.utilities.interactive.greenfield_map)."""

import pandas as pd
import pytest

from steelo.domain.models import PrimaryFeedstock
from steelo.utilities.interactive import greenfield_map


def sample_status_timeseries() -> pd.DataFrame:
    """Two groups: an iron one switching technology and reductant, and a discarded steel EAF."""
    columns = [
        "year",
        "furnace_group_id",
        "product",
        "technology",
        "reductant",
        "status",
        "geo_key",
        "lat",
        "lon",
        "capacity",
        "production",
    ]
    rows = [
        [2025, "P1", "iron", "BF", "coke+pci", "considered", "CHN:CN-NM", 40.0, 110.0, 2_500_000.0, 0.0],
        [2026, "P1", "iron", "BF", "coke+pci", "construction", "CHN:CN-NM", 40.0, 110.0, 2_500_000.0, 0.0],
        [2027, "P1", "iron", "BF", "coke+pci", "operating", "CHN:CN-NM", 40.0, 110.0, 2_500_000.0, 1_500_000.0],
        [2028, "P1", "iron", "SR", "pci", "operating", "CHN:CN-NM", 40.0, 110.0, 2_500_000.0, 2_000_000.0],
        [2026, "P2", "steel", "EAF", None, "considered", "SAU", 24.0, 45.0, 2_500_000.0, 0.0],
        [2027, "P2", "steel", "EAF", None, "discarded", "SAU", 24.0, 45.0, 2_500_000.0, 0.0],
        [2028, "P2", "steel", "EAF", None, "discarded", "SAU", 24.0, 45.0, 2_500_000.0, 0.0],
    ]
    return pd.DataFrame(rows, columns=columns)


def test_pack_groups_run_length_encodes_histories() -> None:
    """Each field's history compresses to [year, value] segments; a persisting value never repeats."""
    payload = greenfield_map.pack_groups(sample_status_timeseries())

    assert payload["years"] == [2025, 2026, 2027, 2028]
    # Lifecycle order for statuses, alphabetical technologies, house order for reductants.
    assert payload["statuses"] == ["considered", "construction", "operating", "discarded"]
    assert payload["techs"] == ["BF", "EAF", "SR"]
    assert payload["reductants"] == ["coke+pci", "pci", "none"]

    p1 = next(group for group in payload["groups"] if group["id"] == "P1")
    assert p1["g"] == "CHN:CN-NM"
    assert p1["p"] == "iron"
    assert (p1["la"], p1["lo"]) == (40.0, 110.0)
    assert p1["cap"] == [[2025, 2.5]]
    assert p1["y1"] == 2028
    assert p1["s"] == [[2025, 0], [2026, 1], [2027, 2]]
    assert p1["t"] == [[2025, 0], [2028, 2]]  # BF, then the switch to SR in 2028
    assert p1["r"] == [[2025, 0], [2028, 1]]  # coke+pci, then pci with the switch
    assert p1["pr"] == [[2025, 0.0], [2027, 1.5], [2028, 2.0]]


def test_pack_groups_tracks_capacity_per_year() -> None:
    """A policy-shrunk group carries its reduced capacity from the year it shrinks, not its first snapshot."""
    timeseries = sample_status_timeseries()
    shrunk = (timeseries["furnace_group_id"] == "P1") & (timeseries["year"] >= 2027)
    timeseries.loc[shrunk, "capacity"] = 2_500_000.0 * 2 / 3

    payload = greenfield_map.pack_groups(timeseries)

    p1 = next(group for group in payload["groups"] if group["id"] == "P1")
    assert p1["cap"] == [[2025, 2.5], [2027, 1.6667]]


def test_pack_groups_labels_missing_reductant_none() -> None:
    """Steel groups run no reductant; the empty value becomes the explicit 'none' option."""
    payload = greenfield_map.pack_groups(sample_status_timeseries())

    p2 = next(group for group in payload["groups"] if group["id"] == "P2")
    assert p2["r"] == [[2026, payload["reductants"].index("none")]]
    assert p2["s"] == [[2026, 0], [2027, 3]]  # considered, then discarded and persisting


def test_pack_groups_rejects_missing_column() -> None:
    """A timeseries missing a required column fails loudly rather than producing an empty map."""
    with pytest.raises(ValueError, match="reductant"):
        greenfield_map.pack_groups(sample_status_timeseries().drop(columns=["reductant"]))


def test_greenfield_charges_keeps_bom_charges_only() -> None:
    """Only feedstock rows that are their technology's metallic charge survive, for greenfield groups only."""
    post_processed = pd.DataFrame(
        {
            "year": [2027, 2027, 2027, 2027],
            "furnace_group_id": ["P1", "P1", "OTHER", "P1"],
            "technology": ["BF", "BF", "BF", "BF"],
            "feedstock": ["io_high", "bio_pci", "io_high", None],
            "demand": [3_400_000.0, 200_000.0, 1_000_000.0, 0.0],
        },
    )
    feedstocks = [PrimaryFeedstock(metallic_charge="io_high", reductant="coke+pci", technology="BF")]

    charges = greenfield_map.greenfield_charges(post_processed, feedstocks, {"P1"})

    assert charges.to_dict("records") == [
        {"furnace_group_id": "P1", "year": 2027, "charge": "io_high", "use_mt": pytest.approx(3.4)},
    ]


def test_pack_groups_embeds_charges_per_year() -> None:
    """A group's charge allocations land under their year as [charge index, Mt] pairs."""
    charges = pd.DataFrame(
        {
            "furnace_group_id": ["P1", "P1", "P1"],
            "year": [2027, 2028, 2028],
            "charge": ["io_high", "io_high", "io_low"],
            "use_mt": [3.4, 3.0, 0.5],
        },
    )

    payload = greenfield_map.pack_groups(sample_status_timeseries(), charges)

    assert payload["charges"] == ["io_high", "io_low"]
    p1 = next(group for group in payload["groups"] if group["id"] == "P1")
    assert p1["c"] == {2027: [[0, 3.4]], 2028: [[0, 3.0], [1, 0.5]]}
    p2 = next(group for group in payload["groups"] if group["id"] == "P2")
    assert "c" not in p2
