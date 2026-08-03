"""Tests for DataManager download-cache location resolution."""

from pathlib import Path

from steelo.data.manager import DataManager


def test_cache_dir_defaults_to_steelo_home(monkeypatch, tmp_path):
    """The download cache follows $STEELO_HOME so isolated homes do not share it.

    Two simulations running in parallel with different STEELO_HOMEs must not
    race on the same package download; the cache therefore lives inside the
    active home rather than a hardcoded ~/.steelo path.
    """
    home = tmp_path / "isolated-home"
    monkeypatch.setenv("STEELO_HOME", str(home))

    manager = DataManager()

    assert manager.cache_dir == home / "data_cache"
    assert manager.cache_dir.is_dir()


def test_data_cache_env_override_wins_over_home(monkeypatch, tmp_path):
    """$STEELO_DATA_CACHE overrides the per-home default.

    Packaged apps set version-specific STEELO_HOMEs but share one download cache
    across versions via this override, so users never re-download packages.
    """
    monkeypatch.setenv("STEELO_HOME", str(tmp_path / "versioned-home"))
    shared = tmp_path / "shared-cache"
    monkeypatch.setenv("STEELO_DATA_CACHE", str(shared))

    manager = DataManager()

    assert manager.cache_dir == shared


def test_cache_dir_falls_back_to_default_home(monkeypatch):
    """Without STEELO_HOME set, the cache stays at ~/.steelo/data_cache."""
    monkeypatch.delenv("STEELO_HOME", raising=False)
    monkeypatch.delenv("STEELO_DATA_CACHE", raising=False)

    manager = DataManager()

    assert manager.cache_dir == Path.home() / ".steelo" / "data_cache"


def test_explicit_cache_dir_wins(monkeypatch, tmp_path):
    """An explicitly passed cache_dir overrides any STEELO_HOME setting."""
    monkeypatch.setenv("STEELO_HOME", str(tmp_path / "ignored-home"))
    explicit = tmp_path / "explicit-cache"

    manager = DataManager(cache_dir=explicit)

    assert manager.cache_dir == explicit
