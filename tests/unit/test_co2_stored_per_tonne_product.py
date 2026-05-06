"""Tests for Technology.co2_stored_per_tonne_product and the module-level helper."""

from steelo.domain.models import (
    PrimaryFeedstock,
    Technology,
    co2_stored_per_tonne_from_feedstocks,
)


def _make_feedstock(metallic_charge: str, reductant: str, co2_stored: float | None) -> PrimaryFeedstock:
    pf = PrimaryFeedstock(metallic_charge=metallic_charge, reductant=reductant, technology="BFCCS")
    if co2_stored is not None:
        pf.add_carbon_output("co2_stored", co2_stored)
    return pf


def test_bfccs_coke_pci_picks_max_across_metallic_charges():
    """Reductant filter narrows to Coke+PCI feedstocks, max taken across 3 metallic charges."""
    dbc = [
        _make_feedstock("IO_high", "Coke+PCI", 2.685),
        _make_feedstock("IO_mid", "Coke+PCI", 2.699),
        _make_feedstock("IO_low", "Coke+PCI", 2.853),
        _make_feedstock("IO_high", "Coke+PCI+H2", 2.100),
        _make_feedstock("IO_mid", "Coke+PCI+H2", 2.180),
        _make_feedstock("IO_low", "Coke+PCI+H2", 2.225),
    ]
    tech = Technology(name="BFCCS", product="hot_metal", dynamic_business_case=dbc)

    assert tech.co2_stored_per_tonne_product("Coke+PCI") == 2.853


def test_bfccs_coke_pci_h2_picks_max_across_metallic_charges():
    """Different reductant → different max; confirms filter is load-bearing."""
    dbc = [
        _make_feedstock("IO_high", "Coke+PCI", 2.685),
        _make_feedstock("IO_low", "Coke+PCI", 2.853),
        _make_feedstock("IO_high", "Coke+PCI+H2", 2.100),
        _make_feedstock("IO_low", "Coke+PCI+H2", 2.225),
    ]
    tech = Technology(name="BFCCS", product="hot_metal", dynamic_business_case=dbc)

    assert tech.co2_stored_per_tonne_product("Coke+PCI+H2") == 2.225


def test_unknown_reductant_falls_back_to_all_reductants_max():
    """Reductant absent from BOM → max across all feedstocks (conservative upper bound)."""
    dbc = [
        _make_feedstock("IO_low", "Coke+PCI", 2.853),
        _make_feedstock("IO_low", "Coke+PCI+H2", 2.225),
    ]
    tech = Technology(name="BFCCS", product="hot_metal", dynamic_business_case=dbc)

    assert tech.co2_stored_per_tonne_product("H2") == 2.853


def test_empty_reductant_falls_back_when_bom_has_no_empty_reductant():
    """Empty-string reductant (pre-economics FG / non-reductant tech) absent from BOM →
    all-reductants max. chosen_reductant defaults to "" for FGs that haven't run economics."""
    dbc = [
        _make_feedstock("IO_low", "Coke+PCI", 2.853),
        _make_feedstock("IO_low", "Coke+PCI+H2", 2.225),
    ]
    tech = Technology(name="BFCCS", product="hot_metal", dynamic_business_case=dbc)

    assert tech.co2_stored_per_tonne_product("") == 2.853


def test_empty_reductant_matches_empty_reductant_in_bom():
    """Empty-string reductant that DOES exist in BOM (e.g. BOF-family tech with no reductant
    column) matches directly — the filter picks the matching entry, no fallback triggered."""
    dbc = [
        _make_feedstock("scrap", "", 0.5),
        _make_feedstock("scrap_mid", "", 0.6),
        _make_feedstock("scrap_high", "Coke", 9.9),
    ]
    tech = Technology(name="BOFLIKE", product="liquid_steel", dynamic_business_case=dbc)

    assert tech.co2_stored_per_tonne_product("") == 0.6


def test_empty_dynamic_business_case_returns_zero():
    """Empty list → 0.0; FG with missing BOM contributes nothing to the gate."""
    tech = Technology(name="BFCCS", product="hot_metal", dynamic_business_case=[])

    assert tech.co2_stored_per_tonne_product("Coke+PCI") == 0.0


def test_none_dynamic_business_case_returns_zero():
    """None BOM → 0.0 (no crash). Scan logs a warning for CCS FGs hitting this path."""
    tech = Technology(name="BFCCS", product="hot_metal", dynamic_business_case=None)

    assert tech.co2_stored_per_tonne_product("Coke+PCI") == 0.0


def test_co2_stored_vector_absent_returns_zero():
    """Non-CCS tech without co2_stored in its BOM → 0.0 (gate is a no-op)."""
    dbc = [_make_feedstock("IO_low", "Coke+PCI", None)]
    tech = Technology(name="BF", product="hot_metal", dynamic_business_case=dbc)

    assert tech.co2_stored_per_tonne_product("Coke+PCI") == 0.0


def test_case_sensitive_reductant_filter():
    """Reductant matching is BOM-raw (case-preserved). Any .lower() on either side
    breaks equality and silently falls through to the all-reductants fallback,
    over-blocking by ~25% without warning — guarded against here."""
    dbc = [
        _make_feedstock("IO_low", "Coke+PCI", 2.853),
        _make_feedstock("IO_low", "Coke+PCI+H2", 2.225),
    ]
    tech = Technology(name="BFCCS", product="hot_metal", dynamic_business_case=dbc)

    assert tech.co2_stored_per_tonne_product("Coke+PCI") == 2.853
    assert tech.co2_stored_per_tonne_product("coke+pci") == 2.853


def test_module_level_helper_matches_technology_method():
    """Free function and Technology method produce identical results."""
    dbc = [
        _make_feedstock("IO_high", "Coke+PCI", 2.685),
        _make_feedstock("IO_low", "Coke+PCI", 2.853),
    ]
    tech = Technology(name="BFCCS", product="hot_metal", dynamic_business_case=dbc)

    assert co2_stored_per_tonne_from_feedstocks(dbc, "Coke+PCI") == tech.co2_stored_per_tonne_product("Coke+PCI")


def test_module_level_helper_handles_none_and_empty():
    assert co2_stored_per_tonne_from_feedstocks(None, "Coke+PCI") == 0.0
    assert co2_stored_per_tonne_from_feedstocks([], "Coke+PCI") == 0.0
    assert co2_stored_per_tonne_from_feedstocks(None, "") == 0.0
