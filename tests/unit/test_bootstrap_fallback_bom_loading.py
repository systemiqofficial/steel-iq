from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import steelo.bootstrap as bootstrap
from steelo.adapters.dataprocessing import excel_reader


def test_load_fallback_bom_prefers_config_master_excel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_master_excel = tmp_path / "master_input.xlsx"
    configured_master_excel.write_bytes(b"dummy")

    called: dict[str, Any] = {}

    def fake_read_fallback_bom_definitions(path: Path) -> dict[str, str]:
        called["path"] = path
        return {"TECH_A": "io_high"}

    class FailIfInstantiatedResolver:
        def __init__(self, *_: Any, **__: Any) -> None:
            raise AssertionError("DataPathResolver should not be used when config.master_excel_path exists")

    caplog.set_level("INFO")
    monkeypatch.setattr(bootstrap, "DataPathResolver", FailIfInstantiatedResolver)
    monkeypatch.setattr(excel_reader, "read_fallback_bom_definitions", fake_read_fallback_bom_definitions)

    mapping = bootstrap._load_default_metallic_charge_per_technology(
        config_master_excel_path=configured_master_excel,
        fixtures_dir=tmp_path / "fixtures",
    )

    assert mapping == {"TECH_A": "io_high"}
    assert called["path"] == configured_master_excel
    assert "Loading fallback BOM definitions from:" in caplog.text
    assert "Loaded default metallic charge mapping entries: 1" in caplog.text


def test_load_fallback_bom_falls_back_to_resolver(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixtures_dir = tmp_path / "data" / "fixtures"
    fixtures_dir.mkdir(parents=True)

    discovered_master_excel = tmp_path / "discovered_master.xlsx"
    discovered_master_excel.write_bytes(b"dummy")

    called: dict[str, Any] = {}

    def fake_read_fallback_bom_definitions(path: Path) -> dict[str, str]:
        called["path"] = path
        return {"TECH_B": "io_low"}

    class DummyResolver:
        def __init__(self, data_directory: Path) -> None:
            called["resolver_data_directory"] = data_directory

        @property
        def fallback_bom_excel_path(self) -> Path:
            return discovered_master_excel

    caplog.set_level("INFO")
    monkeypatch.setattr(bootstrap, "DataPathResolver", DummyResolver)
    monkeypatch.setattr(excel_reader, "read_fallback_bom_definitions", fake_read_fallback_bom_definitions)

    mapping = bootstrap._load_default_metallic_charge_per_technology(
        config_master_excel_path=None,
        fixtures_dir=fixtures_dir,
    )

    assert mapping == {"TECH_B": "io_low"}
    assert called["resolver_data_directory"] == fixtures_dir.parent
    assert called["path"] == discovered_master_excel


def test_load_fallback_bom_falls_back_when_config_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fixtures_dir = tmp_path / "data" / "fixtures"
    fixtures_dir.mkdir(parents=True)

    missing_config_path = tmp_path / "does_not_exist.xlsx"

    discovered_master_excel = tmp_path / "discovered_master.xlsx"
    discovered_master_excel.write_bytes(b"dummy")

    called: dict[str, Any] = {}

    def fake_read_fallback_bom_definitions(path: Path) -> dict[str, str]:
        called["path"] = path
        return {"TECH_C": "io_mid"}

    class DummyResolver:
        def __init__(self, data_directory: Path) -> None:
            called["resolver_data_directory"] = data_directory

        @property
        def fallback_bom_excel_path(self) -> Path:
            return discovered_master_excel

    caplog.set_level("INFO")
    monkeypatch.setattr(bootstrap, "DataPathResolver", DummyResolver)
    monkeypatch.setattr(excel_reader, "read_fallback_bom_definitions", fake_read_fallback_bom_definitions)

    mapping = bootstrap._load_default_metallic_charge_per_technology(
        config_master_excel_path=missing_config_path,
        fixtures_dir=fixtures_dir,
    )

    assert mapping == {"TECH_C": "io_mid"}
    assert called["resolver_data_directory"] == fixtures_dir.parent
    assert called["path"] == discovered_master_excel


def test_load_fallback_bom_handles_read_exception(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    master_excel = tmp_path / "master_input.xlsx"
    master_excel.write_bytes(b"dummy")

    def raising_reader(_: Path) -> dict[str, str]:
        raise ValueError("boom")

    caplog.set_level("WARNING")
    monkeypatch.setattr(excel_reader, "read_fallback_bom_definitions", raising_reader)

    mapping = bootstrap._load_default_metallic_charge_per_technology(
        config_master_excel_path=master_excel,
        fixtures_dir=None,
    )

    assert mapping == {}
    assert "Could not load fallback BOM definitions" in caplog.text


def test_load_fallback_bom_returns_empty_when_resolver_path_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixtures_dir = tmp_path / "data" / "fixtures"
    fixtures_dir.mkdir(parents=True)

    missing_discovered_path = tmp_path / "discovered_missing.xlsx"

    class DummyResolver:
        def __init__(self, _: Path) -> None:
            pass

        @property
        def fallback_bom_excel_path(self) -> Path:
            return missing_discovered_path

    caplog.set_level("WARNING")
    monkeypatch.setattr(bootstrap, "DataPathResolver", DummyResolver)

    mapping = bootstrap._load_default_metallic_charge_per_technology(
        config_master_excel_path=None,
        fixtures_dir=fixtures_dir,
    )

    assert mapping == {}
    assert "Master workbook for fallback BOM definitions not found" in caplog.text


def test_load_fallback_bom_returns_empty_when_no_source(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")
    mapping = bootstrap._load_default_metallic_charge_per_technology(
        config_master_excel_path=None,
        fixtures_dir=None,
    )
    assert mapping == {}
    assert "Default metallic charges will remain empty" in caplog.text
