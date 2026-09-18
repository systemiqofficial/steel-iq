"""Tests for the global motions binding and its ``pam_motions.csv`` flush."""

import csv
from pathlib import Path

import pytest

from steelo import motions
from steelo.capacity_policy.recorder import MOTIONS_COLUMNS


@pytest.fixture(autouse=True)
def unbind_after_test():
    """Module-level binding must never leak between tests."""
    yield
    motions.unbind_global_motions()


def record_close(year: int = 2030) -> None:
    recorder = motions.global_motions_recorder()
    assert recorder is not None
    recorder.record_motion(
        year=year,
        kind="close",
        source="input_data",
        plant_id="plant-1",
        furnace_group_id="fg-1",
        geo_key="DEU",
        old_technology="BF",
        old_capacity_t=2.0,
        owner_id="E1",
        product="iron",
        reductant="Coke+PCI",
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_unbound_flush_writes_nothing(tmp_path: Path):
    """No binding, no artefact — not even an empty file."""
    motions.unbind_global_motions()  # an earlier bootstrap in the session may have left a binding
    motions.flush_global_motions(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_empty_bound_flush_writes_headers_only(tmp_path: Path):
    """A run with no motions still leaves the file, headers intact."""
    motions.bind_global_motions()
    motions.flush_global_motions(tmp_path / "data")

    path = tmp_path / "data" / "pam_motions.csv"
    assert path.exists()
    with path.open(newline="") as handle:
        (header,) = list(csv.reader(handle))
    assert tuple(header) == MOTIONS_COLUMNS


def test_recorded_rows_round_trip(tmp_path: Path):
    motions.bind_global_motions()
    record_close()
    motions.flush_global_motions(tmp_path)

    (row,) = read_rows(tmp_path / "pam_motions.csv")
    assert row["year"] == "2030"
    assert row["kind"] == "close"
    assert row["source"] == "input_data"
    assert row["geo_key"] == "DEU"
    assert row["old_capacity_t"] == "2.0"
    assert row["new_technology"] == ""


def test_rebinding_starts_a_fresh_recorder(tmp_path: Path):
    """Successive runs in one process must not accumulate each other's rows."""
    motions.bind_global_motions()
    record_close()
    motions.bind_global_motions()
    motions.flush_global_motions(tmp_path)

    assert read_rows(tmp_path / "pam_motions.csv") == []
