"""Tests for the capacity world map viewer's site packing (steelo.utilities.interactive.capacity_world_map)."""

import pandas as pd
import pytest

from steelo.capacity_policy.inputs import RegionRow
from steelo.utilities.interactive import capacity_world_map

PLANTS = {
    "P1": {"lat": 39.12346, "lon": 117.2, "greenfield": False},
    "P2": {"lat": 51.0, "lon": 7.0, "greenfield": False},
    "indi_1": {"lat": 24.0, "lon": 45.0, "greenfield": True},
}


def sample_table() -> pd.DataFrame:
    """A Chinese BF that stops after 2026 for a rebuild (two feedstock rows a year) and a German EAF to 2030."""
    columns = ["year", "iso3", "geo_key", "plant_id", "furnace_group_id", "technology", "product", "capacity"]
    rows = []
    for year in (2025, 2026):
        rows.append([year, "CHN", "CHN:CN-HE", "P1", "P1_0", "BF", "iron", 3_000_400.0])
        rows.append([year, "CHN", "CHN:CN-HE", "P1", "P1_0", "BF", "iron", 3_000_400.0])
    for year in range(2025, 2031):
        rows.append([year, "DEU", "DEU", "P2", "P2_0", "EAF", "steel", 1_200_000.0])
    return pd.DataFrame(rows, columns=columns)


def sample_greenfield_status() -> pd.DataFrame:
    """A Saudi greenfield DRI under construction in 2029-2030, plus rows the map must ignore."""
    columns = ["year", "geo_key", "plant_id", "furnace_group_id", "technology", "product", "capacity", "status"]
    rows = [
        [2028, "SAU", "indi_1", "indi_1_0", "DRI", "iron", 2_500_000.0, "announced"],
        [2029, "SAU", "indi_1", "indi_1_0", "DRI", "iron", 2_500_000.0, "construction"],
        [2030, "SAU", "indi_1", "indi_1_0", "DRI", "iron", 2_500_000.0, "construction"],
        [2030, "SAU", "indi_1", "indi_1_1", "BF", "iron", 1_000_000.0, "construction switching technology"],
    ]
    return pd.DataFrame(rows, columns=columns)


def sample_switch_decisions() -> pd.DataFrame:
    """P1's BF → DRI rebuild from 2027 (new technology from 2029) and a second one running past the horizon."""
    columns = ["decision_year", "switch_year", "construction_start_year", "plant_id", "furnace_group_id", "geo_key"]
    columns += ["product", "old_technology", "new_technology", "new_capacity_t"]
    rows = [
        [2025, 2029, 2027.0, "P1", "P1_0", "CHN:CN-HE", "iron", "BF", "DRI", 2_000_000.0],
        [2029, 2033, 2029.0, "P1", "P1_0", "CHN:CN-HE", "iron", "DRI", "BF", 1_500_000.0],
        [2030, 2034, None, "P2", "P2_0", "DEU", "steel", "EAF", "BOF", 1_200_000.0],
    ]
    return pd.DataFrame(rows, columns=columns)


def site(payload: dict, plant_id: str) -> dict:
    return next(s for s in payload["sites"] if s["id"] == plant_id)


def test_pack_sites_dedupes_feedstock_rows_and_encodes_segments() -> None:
    """One unit per group and year in ttpa; identical years merge into one segment, empty years included."""
    payload = capacity_world_map.pack_sites(sample_table(), None, None, PLANTS, {"P1": "Tangshan Works"})

    assert payload["years"] == list(range(2025, 2031))
    assert payload["techs"] == ["BF", "EAF"] and payload["ironTechs"] == ["BF"]
    assert payload["statuses"] == ["operating", "construction"]
    p1 = site(payload, "P1")
    assert p1["segs"] == [[2025, 2026, [["BF", "operating", 3000, None, "P1_0"]]], [2027, 2030, []]]
    assert (p1["name"], p1["iso"], p1["geo"], p1["lat"], p1["lon"]) == (
        "Tangshan Works",
        "CHN",
        "CHN:CN-HE",
        39.1235,
        117.2,
    )
    p2 = site(payload, "P2")
    assert p2["segs"] == [[2025, 2030, [["EAF", "operating", 1200, None, "P2_0"]]]]
    assert (p2["name"], p2["geo"], p2["switches"]) == ("P2", None, [])


def test_pack_sites_adds_rebuild_years_in_the_new_technology() -> None:
    """A decision's rebuild runs from its construction start to the year before the switch, capped at the last year."""
    payload = capacity_world_map.pack_sites(sample_table(), None, sample_switch_decisions(), PLANTS, {})

    p1 = site(payload, "P1")
    assert p1["segs"] == [
        [2025, 2026, [["BF", "operating", 3000, None, "P1_0"]]],
        [2027, 2028, [["DRI", "construction", 2000, "BF", "P1_0"]]],
        [2029, 2030, [["BF", "construction", 1500, "DRI", "P1_0"]]],
    ]
    assert p1["switches"] == [[2025, 2029, "BF", "DRI"], [2029, 2033, "DRI", "BF"]]
    # a decision that never started construction adds no rebuild rows but is still listed
    p2 = site(payload, "P2")
    assert p2["segs"] == [[2025, 2030, [["EAF", "operating", 1200, None, "P2_0"]]]]
    assert p2["switches"] == [[2030, 2034, "EAF", "BOF"]]
    assert payload["techs"] == ["BF", "DRI", "EAF"]


def test_pack_sites_takes_only_construction_rows_of_the_greenfield_table() -> None:
    """New builds come from status == construction; switching rows are left to the decisions file."""
    payload = capacity_world_map.pack_sites(sample_table(), sample_greenfield_status(), None, PLANTS, {})

    new_plant = site(payload, "indi_1")
    assert new_plant["segs"] == [[2025, 2028, []], [2029, 2030, [["DRI", "construction", 2500, None, "indi_1_0"]]]]
    assert (new_plant["name"], new_plant["iso"], new_plant["greenfield"]) == ("New plant indi_1", "SAU", True)
    assert site(payload, "P1")["greenfield"] is False


def test_pack_sites_raises_on_a_plant_without_coordinates() -> None:
    """A plant on the map that the run's plants do not cover fails loudly."""
    plants = {plant_id: plant for plant_id, plant in PLANTS.items() if plant_id != "P2"}

    with pytest.raises(ValueError, match="1 plants on the capacity map have no coordinates"):
        capacity_world_map.pack_sites(sample_table(), None, None, plants, {})


def test_pack_sites_raises_on_a_missing_column() -> None:
    """A table without a required column is reported by name."""
    with pytest.raises(ValueError, match="post-processed table has no plant_id"):
        capacity_world_map.pack_sites(sample_table().drop(columns=["plant_id"]), None, None, PLANTS, {})


def test_pack_sites_keeps_one_country_for_a_focus_map() -> None:
    """With iso3 only that country's plants stay, over all simulated years; a country without plants raises."""
    payload = capacity_world_map.pack_sites(sample_table(), sample_greenfield_status(), None, PLANTS, {}, iso3="CHN")

    assert [s["id"] for s in payload["sites"]] == ["P1"]
    assert payload["years"] == list(range(2025, 2031))
    assert payload["techs"] == ["BF"]
    # plants elsewhere need no coordinates
    china_only = {"P1": PLANTS["P1"]}
    assert len(capacity_world_map.pack_sites(sample_table(), None, None, china_only, {}, iso3="CHN")["sites"]) == 1
    with pytest.raises(ValueError, match="The run has no plants in JPN"):
        capacity_world_map.pack_sites(sample_table(), None, None, PLANTS, {}, iso3="JPN")


def test_focus_config_groups_provinces_that_share_a_region_name() -> None:
    """A region name held by one province is no group; types carry key and exempt; no rows give no groups."""
    rows = [
        RegionRow(geo_key="CHN:CN-HE", region_name="Jing-Jin-Ji", type="key"),
        RegionRow(geo_key="CHN:CN-BJ", region_name="Jing-Jin-Ji", type="key"),
        RegionRow(geo_key="CHN:CN-XJ", region_name="Xinjiang", type=None),
        RegionRow(geo_key="CHN:CN-XZ", region_name=None, type="exempt"),
    ]

    assert capacity_world_map.focus_config("CHN", rows) == {
        "iso3": "CHN",
        "groups": {"CHN:CN-HE": "Jing-Jin-Ji", "CHN:CN-BJ": "Jing-Jin-Ji"},
        "types": {"CHN:CN-HE": "key", "CHN:CN-BJ": "key", "CHN:CN-XZ": "exempt"},
    }
    assert capacity_world_map.focus_config("CHN", []) == {"iso3": "CHN", "groups": {}, "types": {}}


def test_source_line_names_the_input_data_sets() -> None:
    """gem_unit reads as GEM, other sources are upper-cased, and an empty list reads as GEM only."""
    assert (
        capacity_world_map.source_line(["gem_unit", "external"])
        == "Source: Steel-IQ model (with GEM and EXTERNAL input data)"
    )
    assert (
        capacity_world_map.source_line(["gem_unit", "external", "cisri"])
        == "Source: Steel-IQ model (with GEM, EXTERNAL and CISRI input data)"
    )
    assert capacity_world_map.source_line(["gem_unit"]) == "Source: Steel-IQ model (with GEM input data)"
    assert capacity_world_map.source_line([]) == "Source: Steel-IQ model (with GEM input data)"
