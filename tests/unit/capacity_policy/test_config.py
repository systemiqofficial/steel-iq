"""Validation tests for CapacityPolicyConfig and its nesting on SimulationConfig."""

import pytest

from steelo.capacity_policy.config import CapacityPolicyConfig
from steelo.domain.models import Year
from steelo.simulation import SimulationConfig


def test_defaults_are_dormant():
    """The default config is disabled with the spec's parameter defaults."""
    config = CapacityPolicyConfig()
    assert config.enabled is False
    assert config.replacement_ratio == 1.5
    assert config.emission_intense_penalty_divisor == 1.5
    assert config.min_utilization_for_renovation == 0.25
    assert config.utilization_window_years == 2
    assert config.inter_company_swap_cutoff_year == 2028
    assert config.banked_credit_rule == "reassign"
    assert config.credit_validity_years is None
    assert config.capacity_pool_max_retry_years == 5
    assert config.reline_counts_as_replace is False


def test_unknown_banked_credit_rule_raises():
    """banked_credit_rule is validated against the pool's rule set."""
    with pytest.raises(ValueError, match="Unknown banked_credit_rule 'vanish'"):
        CapacityPolicyConfig(banked_credit_rule="vanish")


def test_non_positive_ratios_and_window_raise():
    """Ratios and the utilisation window must be positive."""
    with pytest.raises(ValueError, match="replacement_ratio must be positive"):
        CapacityPolicyConfig(replacement_ratio=0.0)
    with pytest.raises(ValueError, match="emission_intense_penalty_divisor must be positive"):
        CapacityPolicyConfig(emission_intense_penalty_divisor=-1.5)
    with pytest.raises(ValueError, match="utilization_window_years must be positive"):
        CapacityPolicyConfig(utilization_window_years=0)


def test_non_positive_validity_and_retry_cap_raise():
    """A shelf life is optional but, once set, must be a real number of years."""
    with pytest.raises(ValueError, match="credit_validity_years must be positive when set"):
        CapacityPolicyConfig(credit_validity_years=0)
    with pytest.raises(ValueError, match="capacity_pool_max_retry_years must be positive"):
        CapacityPolicyConfig(capacity_pool_max_retry_years=0)
    assert CapacityPolicyConfig(credit_validity_years=None).credit_validity_years is None


def test_utilization_floor_outside_unit_interval_raises():
    """The renovation utilisation floor is a share and must sit within [0, 1]."""
    with pytest.raises(ValueError, match=r"min_utilization_for_renovation must be within \[0, 1\]"):
        CapacityPolicyConfig(min_utilization_for_renovation=1.2)


def test_simulation_config_nests_dormant_default(tmp_path):
    """SimulationConfig carries a default CapacityPolicyConfig, off by default."""
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path="dummy.xlsx",
        output_dir=str(tmp_path / "output"),
    )
    assert isinstance(config.capacity_policy, CapacityPolicyConfig)
    assert config.capacity_policy.enabled is False
