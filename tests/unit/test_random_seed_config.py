# tests/unit/test_random_seed_config.py
"""Tests for the centralised random-seed configuration (SimulationConfig.random_seed)."""

import random
from pathlib import Path

import numpy as np
import pytest

from steelo.domain import Year
from steelo.domain.trade_modelling import trade_lp_modelling
from steelo.simulation import SimulationConfig
from steelo.simulation_types import get_default_technology_settings
from steeloweb import forms as steeloweb_forms


@pytest.fixture
def base_config_kwargs():
    """Minimal SimulationConfig kwargs for construction in unit tests."""
    return dict(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output_random_seed_test"),
        technology_settings=get_default_technology_settings(),
    )


def test_simulation_config_random_seed_default(base_config_kwargs):
    """`SimulationConfig.random_seed` defaults to 42."""
    config = SimulationConfig(**base_config_kwargs)
    assert config.random_seed == 42


def test_simulation_config_random_seed_propagates_to_geo_config(base_config_kwargs):
    """Top-level `random_seed` is propagated into `geo_config.random_seed` via __post_init__."""
    config = SimulationConfig(**base_config_kwargs, random_seed=99)
    assert config.random_seed == 99
    assert config.geo_config.random_seed == 99


def test_trade_lp_model_stores_random_seed():
    """`TradeLPModel` stores the seed passed in for later use by the HiGHS solver."""
    model = trade_lp_modelling.TradeLPModel(lp_epsilon=1e-3, random_seed=777)
    assert model.random_seed == 777


def test_python_random_is_deterministic_given_seed():
    """After `random.seed(n)`, `random.random()` returns a deterministic value.

    Guards the contract that `bootstrap_simulation` relies on when it calls
    `random.seed(config.random_seed)`.
    """
    random.seed(123)
    first = random.random()
    random.seed(123)
    second = random.random()
    assert first == second


def test_numpy_random_is_deterministic_given_seed():
    """After `np.random.seed(n)`, `np.random.rand()` returns a deterministic value."""
    np.random.seed(456)
    first = np.random.rand()
    np.random.seed(456)
    second = np.random.rand()
    assert first == second


def _form_data_with_seed(randomise: bool, seed_value=42):
    """Build a minimal valid ModelRunCreateForm payload for clean() testing.

    Only the seed-related fields matter for these tests; everything else is left
    blank or defaulted. The form's other `clean()` defaults handle the rest.
    """
    return {
        "name": "test run",
        "randomise_seed": "on" if randomise else "",
        "random_seed": str(seed_value) if seed_value is not None else "",
        "start_year": "2025",
        "end_year": "2050",
    }


@pytest.mark.django_db
def test_form_clean_keeps_user_seed_when_checkbox_unticked():
    """When `randomise_seed` is unticked, the user's `random_seed` value is preserved."""
    form = steeloweb_forms.ModelRunCreateForm(data=_form_data_with_seed(randomise=False, seed_value=7))
    # Trigger validation. We only care about seed-related cleaned_data; ignore other
    # validation errors from required fields that aren't relevant here.
    form.is_valid()
    assert form.cleaned_data.get("random_seed") == 7
    # `randomise_seed` is a UI-only flag — clean() should strip it.
    assert "randomise_seed" not in form.cleaned_data


@pytest.mark.django_db
def test_form_clean_draws_fresh_seed_when_checkbox_ticked():
    """When `randomise_seed` is ticked, `clean()` replaces `random_seed` with a fresh int."""
    form = steeloweb_forms.ModelRunCreateForm(data=_form_data_with_seed(randomise=True, seed_value=42))
    form.is_valid()
    seed = form.cleaned_data.get("random_seed")
    assert isinstance(seed, int)
    assert 0 <= seed < 2**31
    # `randomise_seed` is a UI-only flag — clean() should strip it.
    assert "randomise_seed" not in form.cleaned_data
