"""Tests for bootstrap-time validation of EF coverage and material invariance."""

import logging
from pathlib import Path

import pytest

from steelo.domain.models import Environment, PrimaryFeedstock, TechnologyEmissionFactors, Year
from steelo.simulation import SimulationConfig


def _make_env(tmp_path: Path) -> Environment:
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2027),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF\nBF,YES\n", encoding="utf-8")
    return Environment(config=config, tech_switches_csv=tech_switches_csv)


def _feedstock(technology: str, metallic_charge: str, reductant: str, quantity: float = 1.5) -> PrimaryFeedstock:
    pf = PrimaryFeedstock(metallic_charge=metallic_charge, reductant=reductant, technology=technology)
    pf.required_quantity_per_ton_of_product = quantity
    return pf


def _ef(
    technology: str, metallic_charge: str, reductant: str, boundary: str = "rs-inspired"
) -> TechnologyEmissionFactors:
    return TechnologyEmissionFactors(
        business_case=f"{metallic_charge}_{reductant}_{technology}",
        technology=technology,
        boundary=boundary,
        metallic_charge=metallic_charge,
        reductant=reductant,
        direct_ghg_factor=1.0,
        direct_with_biomass_ghg_factor=1.0,
        indirect_ghg_factor=0.5,
    )


def test_ef_initiation_accepts_full_coverage(tmp_path: Path) -> None:
    """A complete EF table for the configured boundary initialises without error."""
    env = _make_env(tmp_path)
    env.initiate_dynamic_feedstocks([_feedstock("BF", "IO_low", "Coke+PCI")])

    env.initiate_technology_emission_factors([_ef("BF", "IO_low", "Coke+PCI")])

    assert len(env.technology_emission_factors) == 1


def test_ef_initiation_raises_on_missing_business_case_key(tmp_path: Path) -> None:
    """A business-case key without an EF row fails at load time, not mid-run."""
    env = _make_env(tmp_path)
    env.initiate_dynamic_feedstocks([_feedstock("BF", "IO_low", "Coke+PCI"), _feedstock("BF", "IO_low", "Bio-PCI")])

    with pytest.raises(ValueError, match="incomplete for boundary"):
        env.initiate_technology_emission_factors([_ef("BF", "IO_low", "Coke+PCI")])


def test_ef_initiation_raises_on_empty_table_with_feedstocks(tmp_path: Path) -> None:
    """An empty EF table with business cases present is a load-time error."""
    env = _make_env(tmp_path)
    env.initiate_dynamic_feedstocks([_feedstock("BF", "IO_low", "Coke+PCI")])

    with pytest.raises(ValueError, match="No technology emission factors"):
        env.initiate_technology_emission_factors([])


def test_ef_initiation_raises_when_configured_boundary_absent(tmp_path: Path) -> None:
    """EF rows that never cover the configured boundary fail loudly."""
    env = _make_env(tmp_path)
    env.initiate_dynamic_feedstocks([_feedstock("BF", "IO_low", "Coke+PCI")])

    with pytest.raises(ValueError, match="rs-inspired"):
        env.initiate_technology_emission_factors([_ef("BF", "IO_low", "Coke+PCI", boundary="worldsteel_opt_credits")])


def test_ef_initiation_skips_validation_without_feedstocks(tmp_path: Path) -> None:
    """Test environments without business cases can still set an empty EF list."""
    env = _make_env(tmp_path)

    env.initiate_technology_emission_factors([])

    assert env.technology_emission_factors == []


def test_invariance_warns_on_charge_missing_for_a_reductant(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A metallic charge authored for only one of a tech's reductants is flagged."""
    env = _make_env(tmp_path)
    env.initiate_dynamic_feedstocks(
        [
            _feedstock("DRI", "IO_low", "natural_gas"),
            _feedstock("DRI", "IO_low", "hydrogen"),
            _feedstock("DRI", "IO_high", "natural_gas"),
        ]
    )

    with caplog.at_level(logging.WARNING):
        env.validate_reductant_material_invariance()

    assert any("authored only for reductant" in message for message in caplog.messages)


def test_invariance_warns_on_differing_quantities(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Required quantities differing across reductants are flagged."""
    env = _make_env(tmp_path)
    env.initiate_dynamic_feedstocks(
        [
            _feedstock("DRI", "IO_low", "natural_gas", quantity=1.5),
            _feedstock("DRI", "IO_low", "hydrogen", quantity=1.6),
        ]
    )

    with caplog.at_level(logging.WARNING):
        env.validate_reductant_material_invariance()

    assert any("material requirements" in message for message in caplog.messages)


def test_invariance_silent_on_identical_authoring(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Identical material authoring across reductants produces no warnings."""
    env = _make_env(tmp_path)
    env.initiate_dynamic_feedstocks(
        [
            _feedstock("DRI", "IO_low", "natural_gas"),
            _feedstock("DRI", "IO_low", "hydrogen"),
        ]
    )

    with caplog.at_level(logging.WARNING):
        env.validate_reductant_material_invariance()

    assert not [message for message in caplog.messages if "[REDUCTANT DATA]" in message]
