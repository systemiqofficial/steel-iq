import importlib
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
SCRIPTS = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_console_script_target_is_importable_callable(name):
    """Every `[project.scripts]` entry points at a callable that imports.

    Notes:
        An entry whose module or function is missing still installs as a command, then fails on start.
    """
    module_path, attribute = SCRIPTS[name].split(":")
    module = importlib.import_module(module_path)

    assert callable(getattr(module, attribute, None)), f"{name} -> {SCRIPTS[name]}"
