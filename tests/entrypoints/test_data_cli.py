import sys

from steelo.entrypoints import data_cli


def test_steelo_data_download_specific_package_force(monkeypatch):
    calls: dict[str, tuple[str, str | None, bool]] = {}

    class DummyManager:
        def __init__(self, cache_dir=None):
            self.cache_dir = cache_dir

        def download_package(self, package_name, version=None, force=False):
            calls["download"] = (package_name, version, force)

    monkeypatch.setattr(data_cli, "DataManager", DummyManager)
    monkeypatch.setattr(sys, "argv", ["steelo-data-download", "--package", "master-input", "--force"])

    result = data_cli.steelo_data_download()

    assert result == "Download complete!"
    assert calls["download"] == ("master-input", None, True)
