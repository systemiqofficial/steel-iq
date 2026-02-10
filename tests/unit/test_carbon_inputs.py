"""Tests for carbon_inputs and carbon_outputs on PrimaryFeedstock."""

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


# --- carbon_inputs domain model tests ---


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


# --- carbon_outputs domain model tests ---


def test_add_carbon_output_stores_value():
    """add_carbon_output() stores the value in carbon_outputs, not outputs."""
    feedstock = _make_feedstock()
    feedstock.add_carbon_output("co2_stored", 0.3)

    assert feedstock.carbon_outputs == {"co2_stored": 0.3}
    assert "co2_stored" not in feedstock.outputs


def test_add_carbon_output_rejects_none():
    """add_carbon_output() raises ValueError when amount is None."""
    feedstock = _make_feedstock()
    with pytest.raises(ValueError, match="Carbon output amount cannot be None"):
        feedstock.add_carbon_output("co2_stored", None)


def test_add_carbon_output_multiple_vectors():
    """Multiple CO2 output vectors stored independently."""
    feedstock = _make_feedstock()
    feedstock.add_carbon_output("co2_stored", 0.3)
    feedstock.add_carbon_output("co2_slip", 0.05)
    feedstock.add_carbon_output("co2_utilised", 0.1)

    assert feedstock.carbon_outputs == {
        "co2_stored": 0.3,
        "co2_slip": 0.05,
        "co2_utilised": 0.1,
    }


# --- carbon_inputs serialisation round-trip tests ---


def test_carbon_inputs_round_trip():
    """carbon_inputs survives from_domain -> to_domain round trip."""
    feedstock = _make_feedstock()
    feedstock.add_carbon_input("co2_inlet", 0.5)
    feedstock.add_energy_requirement("electricity", 1.2)

    db_model = PrimaryFeedstockInDb.from_domain(feedstock)
    restored = db_model.to_domain

    assert restored.carbon_inputs == {"co2_inlet": 0.5}
    assert restored.energy_requirements == {"electricity": 1.2}
    assert "co2_inlet" not in restored.energy_requirements


def test_carbon_inputs_defaults_empty():
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


# --- carbon_outputs serialisation round-trip tests ---


def test_carbon_outputs_round_trip():
    """carbon_outputs survives from_domain -> to_domain round trip."""
    feedstock = _make_feedstock()
    feedstock.add_carbon_output("co2_stored", 0.3)
    feedstock.add_carbon_output("co2_slip", 0.05)

    db_model = PrimaryFeedstockInDb.from_domain(feedstock)
    restored = db_model.to_domain

    assert restored.carbon_outputs == {"co2_stored": 0.3, "co2_slip": 0.05}
    assert "co2_stored" not in restored.outputs


def test_carbon_outputs_defaults_empty():
    """Old JSON without carbon_outputs field deserialises to empty dict."""
    db_model = PrimaryFeedstockInDb(
        metallic_charge="scrap",
        reductant="",
        technology="EAF",
        outputs={"steel": 1.0},
    )
    restored = db_model.to_domain

    assert restored.carbon_outputs == {}
    assert restored.outputs == {"steel": 1.0}


# --- cost_breakdown_keys exclusion ---


def test_co2_inputs_not_in_cost_breakdown_keys():
    """co2_inlet stored in carbon_inputs cannot leak into cost_breakdown_keys.

    cost_breakdown_keys collects from energy_requirements + secondary_feedstock only.
    """
    feedstock = _make_feedstock()
    feedstock.add_energy_requirement("electricity", 1.2)
    feedstock.add_carbon_input("co2_inlet", 0.5)

    all_keys: set[str] = set()
    for key in feedstock.energy_requirements:
        all_keys.add(normalize_energy_key(key))
    for key in feedstock.secondary_feedstock:
        all_keys.add(normalize_energy_key(key))

    assert "electricity" in all_keys
    assert "co2_inlet" not in all_keys


def test_co2_outputs_not_in_cost_breakdown_keys():
    """CO2 output vectors in carbon_outputs cannot leak into cost_breakdown_keys."""
    feedstock = _make_feedstock()
    feedstock.add_energy_requirement("electricity", 1.2)
    feedstock.add_carbon_output("co2_stored", 0.3)
    feedstock.add_carbon_output("co2_slip", 0.05)

    all_keys: set[str] = set()
    for key in feedstock.energy_requirements:
        all_keys.add(normalize_energy_key(key))
    for key in feedstock.secondary_feedstock:
        all_keys.add(normalize_energy_key(key))

    assert "electricity" in all_keys
    assert "co2_stored" not in all_keys
    assert "co2_slip" not in all_keys
