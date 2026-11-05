"""Tests for the reductant-use viewer's row packing (steelo.utilities.interactive.reductant_use)."""

import pandas as pd
import pytest

from steelo.domain.models import PrimaryFeedstock
from steelo.utilities.interactive import reductant_use


def sample_post_processed() -> pd.DataFrame:
    """A BF over two feedstock rows, a DRI, and steel rows (EAF) that must stay out of scope."""
    columns = [
        "year",
        "geo_key",
        "furnace_group_id",
        "technology",
        "product",
        "production",
        "chosen_reductant",
        "feedstock",
        "demand",
    ]
    rows = [
        # Same furnace group and year, two feedstock rows → production must count once
        [2025, "CHN:CN-HE", "P1_0", "BF", "iron", 2_000_000.0, "coke+pci", "io_low", 1_500_000.0],
        [2025, "CHN:CN-HE", "P1_0", "BF", "iron", 2_000_000.0, "coke+pci", "io_high", 700_000.0],
        [2025, "DEU", "P2_0", "DRI", "iron", 1_000_000.0, "natural_gas", "io_mid", 1_500_000.0],
        # Steelmaking never uses a reductant — excluded, including its procurement pseudo-feedstock
        [2025, "DEU", "P3_0", "EAF", "steel", 1_000_000.0, None, "scrap", 1_100_000.0],
        [2025, "DEU", "P3_0", "EAF", "steel", 1_000_000.0, None, "bio_pci", 50_000.0],
    ]
    return pd.DataFrame(rows, columns=columns)


def primary_feedstock(
    technology: str, metallic_charge: str, reductant: str, required: float, energy: dict[str, float]
) -> PrimaryFeedstock:
    """A minimal Bill of Materials entry with a required quantity and energy intensities."""
    feedstock = PrimaryFeedstock(metallic_charge=metallic_charge, reductant=reductant, technology=technology)
    feedstock.required_quantity_per_ton_of_product = required
    feedstock.energy_requirements = energy
    return feedstock


def sample_bom() -> list[PrimaryFeedstock]:
    """BOM entries matching the sample table; technologies lower-cased as the repository loads them."""
    return [
        primary_feedstock("bf", "io_low", "coke+pci", 1.5, {"coking_coal": 0.4, "pci": 0.15, "electricity": 300.0}),
        primary_feedstock("bf", "io_high", "coke+pci", 1.4, {"coking_coal": 0.35, "pci": 0.12, "electricity": 300.0}),
        primary_feedstock("dri", "io_mid", "natural_gas", 1.5, {"natural_gas": 2000.0, "electricity": 100.0}),
        # Hydrogen route deployed by no furnace group in the sample run
        primary_feedstock("dri", "io_mid", "hydrogen", 1.5, {"hydrogen": 0.05, "electricity": 1800.0}),
        primary_feedstock("eaf", "scrap", "", 1.1, {"electricity": 500.0}),
    ]


def test_aggregate_counts_each_furnace_group_year_once_and_sums_carriers() -> None:
    """Production dedupes the feedstock rows; reductant quantities sum attribution over them."""
    carriers, aggregated = reductant_use.aggregate_reductant_use(sample_post_processed(), sample_bom())

    # Electricity is auxiliary energy, never a chosen reductant here → not a carrier at all;
    # the undeployed hydrogen route ends up all-zero → dropped.
    assert carriers == ["coking_coal", "natural_gas", "pci"]
    assert "electricity" not in aggregated.columns
    assert set(aggregated["technology"]) == {"BF", "DRI"}  # no steel rows
    bf = aggregated[aggregated["technology"] == "BF"].iloc[0]
    assert bf["reductant"] == "coke+pci"
    assert bf["production_mt"] == pytest.approx(2.0)
    assert bf["n"] == 1
    # io_low: 1.5 Mt / 1.5 = 1 Mt product; io_high: 0.7 / 1.4 = 0.5 Mt product
    assert bf["coking_coal"] == pytest.approx(1.0 * 0.4 + 0.5 * 0.35)  # Mt, the coke component
    assert bf["pci"] == pytest.approx(1.0 * 0.15 + 0.5 * 0.12)

    dri = aggregated[aggregated["technology"] == "DRI"].iloc[0]
    assert dri["reductant"] == "natural_gas"
    assert dri["natural_gas"] == pytest.approx((1.5 / 1.5) * 2000.0 / 1000)  # kWh → TWh
    assert dri["coking_coal"] == 0.0


def test_aggregate_without_feedstocks_keeps_production_only() -> None:
    """No Bill of Materials → no carriers, but the iron production-by-reductant cube survives."""
    carriers, aggregated = reductant_use.aggregate_reductant_use(sample_post_processed(), [])

    assert carriers == []
    assert set(aggregated.columns) == {"year", "geo", "technology", "reductant", "n", "production_mt"}
    assert aggregated["production_mt"].sum() == pytest.approx(3.0)  # steel's 1 Mt excluded


def test_aggregate_warns_on_missing_bom_entry_for_a_metallic_charge(caplog) -> None:
    """A feedstock that is a metallic charge elsewhere but has no entry for its triple is warned about."""
    # Drop the BF io_low entry but keep io_low as a DRI metallic charge, so the BF triple is a real gap.
    bom = sample_bom()[1:]
    bom.append(primary_feedstock("dri", "io_low", "natural_gas", 1.5, {"natural_gas": 2000.0}))

    with caplog.at_level("WARNING", logger="steelo.utilities.interactive.reductant_use"):
        carriers, aggregated = reductant_use.aggregate_reductant_use(sample_post_processed(), bom)

    assert "BF_io_low_coke+pci" in caplog.text
    bf = aggregated[aggregated["technology"] == "BF"].iloc[0]
    assert bf["coking_coal"] == pytest.approx(0.5 * 0.35)  # only the io_high row contributes


def test_aggregate_rejects_unknown_reductant_component() -> None:
    """A reductant component without a mapped carrier fails loudly instead of reading as 0."""
    bom = [primary_feedstock("bf", "io_low", "coke+unobtainium", 1.5, {"coking_coal": 0.4})]

    with pytest.raises(ValueError, match="unobtainium"):
        reductant_use.aggregate_reductant_use(sample_post_processed(), bom)


def test_aggregate_warns_on_component_without_its_own_intensity(caplog) -> None:
    """An entry whose reductant names a component with no intensity vector is warned about."""
    bom = [primary_feedstock("bf", "io_low", "coke+pci", 1.5, {"coking_coal": 0.4})]  # no pci vector

    with caplog.at_level("WARNING", logger="steelo.utilities.interactive.reductant_use"):
        carriers, aggregated = reductant_use.aggregate_reductant_use(sample_post_processed(), bom)

    assert "bf_io_low_coke+pci" in caplog.text
    assert carriers == ["coking_coal"]  # the pci carrier stays all-zero and is dropped


def test_aggregate_rejects_table_without_required_columns() -> None:
    """A table missing the reductant column fails loudly rather than producing an empty chart."""
    with pytest.raises(ValueError, match="chosen_reductant"):
        reductant_use.aggregate_reductant_use(sample_post_processed().drop(columns=["chosen_reductant"]), sample_bom())


def test_aggregate_falls_back_to_iso3_without_geo_key() -> None:
    """Tables from runs that predate the geo_key column are keyed by country."""
    table = sample_post_processed().rename(columns={"geo_key": "iso3"})
    _, aggregated = reductant_use.aggregate_reductant_use(table, [])

    assert set(aggregated["geo"]) == {"CHN:CN-HE", "DEU"}


def test_pack_rows_compacts_aggregates() -> None:
    """Rows carry short keys and carrier quantities in carrier order, to four decimals."""
    carriers, aggregated = reductant_use.aggregate_reductant_use(sample_post_processed(), sample_bom())
    packed = reductant_use.pack_rows(aggregated, carriers)

    dri = next(row for row in packed if row["t"] == "DRI")
    assert dri == {
        "y": 2025,
        "g": "DEU",
        "t": "DRI",
        "r": "natural_gas",
        "n": 1,
        "pr": 1.0,
        "e": [0.0, 2.0, 0.0],
    }


def test_carrier_meta_labels_and_units() -> None:
    """Carrier metadata pairs each key with its display label and aggregation unit."""
    assert reductant_use.carrier_meta(["coking_coal", "electricity"]) == [
        {"key": "coking_coal", "label": "Coking coal", "unit": "Mt"},
        {"key": "electricity", "label": "Electricity", "unit": "TWh"},
    ]
