"""The viewers read and write UTF-8 whatever the platform's default text encoding is."""

import io
from pathlib import Path

import pytest

from steelo.utilities.interactive import (
    InteractivePlotter,
    capacity_world_map,
    embedded_emissions_map,
    greenfield_map,
    trade_allocations,
)


MOJIBAKE = "â€"  # what the UTF-8 bytes of a typographic dash or quote look like read as cp1252


@pytest.fixture
def windows_default_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make pathlib's text I/O fall back to cp1252, the Windows default, when no encoding is given."""
    monkeypatch.setattr(io, "text_encoding", lambda encoding, stacklevel=2: "cp1252" if encoding is None else encoding)


@pytest.mark.parametrize("module", [capacity_world_map, embedded_emissions_map, greenfield_map, trade_allocations])
def test_write_viewer_writes_utf8(module, tmp_path: Path, windows_default_encoding: None) -> None:
    # The inlined plotly.js / deck.gl bundles hold characters that cp1252 cannot encode.
    path = module.write_viewer({"chartTitle": "t"}, {}, tmp_path / "viewer.html")

    html = path.read_bytes().decode("utf-8")
    assert not set(html) <= set(map(chr, range(128))), "expected non-ASCII content from the inlined libraries"
    assert MOJIBAKE not in html


def test_plotter_write_writes_utf8(tmp_path: Path, windows_default_encoding: None) -> None:
    plotter = InteractivePlotter(tmp_path / "plots", [], run_title="sim_test")

    path = plotter._write("emissions.html", {"chartTitle": "t"}, {})

    assert MOJIBAKE not in path.read_bytes().decode("utf-8")
