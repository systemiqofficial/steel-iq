"""Tests for carbon_inputs field on PrimaryFeedstock (Stage 2 of carbon vector changes)."""

import pytest

from steelo.domain.models import PrimaryFeedstock
from steelo.adapters.repositories.json_repository import PrimaryFeedstockInDb
from steelo.utilities.utils import normalize_energy_key


def _make_feedstock(**overrides):
    """Create a minimal PrimaryFeedstock for testing."""
    defaults = {
        "metallic_charge": "scrap",
        "reductant": "",
        "technology": "EAF",
    }
    defaults.update(overrides)
    return PrimaryFeedstock(**defaults)


# --- Domain model tests ---


def test_add_carbon_input_stores_value():
    """add_carbon_input() stores the value in carbon_inputs, not energy_requirements."""
    feedstock = _make_feedstock()
    feedstock.add_carbon_input("co2_inlet", 0.5)

    assert feedstock.carbon_inputs == {"co2_inlet": 0.5}
    assert "co2_inlet" not in feedstock.energy_requirements


def test_add_carbon_input_rejects_none():
    """add_carbon_input() raises ValueError when amount is None."""
    feedstock = _make_feedstock()
    with pytest.raises(ValueError, match="Carbon input amount cannot be None"):
        feedstock.add_carbon_input("co2_inlet", None)


# --- Serialisation round-trip tests ---


def test_primary_feedstock_in_db_round_trip():
    """carbon_inputs survives from_domain → to_domain round trip."""
    feedstock = _make_feedstock()
    feedstock.add_carbon_input("co2_inlet", 0.5)
    feedstock.add_energy_requirement("electricity", 1.2)

    db_model = PrimaryFeedstockInDb.from_domain(feedstock)
    restored = db_model.to_domain

    assert restored.carbon_inputs == {"co2_inlet": 0.5}
    assert restored.energy_requirements == {"electricity": 1.2}
    assert "co2_inlet" not in restored.energy_requirements


def test_primary_feedstock_in_db_defaults_empty_carbon_inputs():
    """Old JSON without carbon_inputs field deserialises to empty dict."""
    db_model = PrimaryFeedstockInDb(
        metallic_charge="scrap",
        reductant="",
        technology="EAF",
        energy_requirements={"electricity": 1.2},
    )
    restored = db_model.to_domain

    assert restored.carbon_inputs == {}
    assert restored.energy_requirements == {"electricity": 1.2}


# --- cost_breakdown_keys exclusion ---


def test_co2_inlet_not_in_energy_requirements():
    """co2_inlet stored in carbon_inputs cannot leak into cost_breakdown_keys.

    cost_breakdown_keys (in initiate_dynamic_feedstocks) collects from
    energy_requirements + secondary_feedstock only. Verify co2_inlet is
    absent from those dicts when using add_carbon_input().
    """
    feedstock = _make_feedstock()
    feedstock.add_energy_requirement("electricity", 1.2)
    feedstock.add_carbon_input("co2_inlet", 0.5)

    # Replicate the cost_breakdown_keys collection logic
    all_keys: set[str] = set()
    for key in feedstock.energy_requirements:
        all_keys.add(normalize_energy_key(key))
    for key in feedstock.secondary_feedstock:
        all_keys.add(normalize_energy_key(key))

    assert "electricity" in all_keys
    assert "co2_inlet" not in all_keys
