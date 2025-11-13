"""
Central helpers for diagnostics/observability during long simulations.

Diagnostics are enabled by environment variables so production runs remain unaffected:

    STEEL_DIAGNOSTICS=1                -> enable diagnostics
    STEEL_DIAGNOSTICS_DETAIL=summary   -> (default) limit heavy exports to late years
    STEEL_DIAGNOSTICS_DETAIL=full      -> always capture detailed outputs
    STEEL_DIAGNOSTICS_PATH=...         -> optional override for base output directory
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Iterable, Sequence

DIAGNOSTICS_ENABLED = os.getenv("STEEL_DIAGNOSTICS", "1") == "1"
DIAGNOSTICS_DETAIL = os.getenv("STEEL_DIAGNOSTICS_DETAIL", "summary").lower()
DIAGNOSTICS_BASE_PATH = Path(os.getenv("STEEL_DIAGNOSTICS_PATH", "output/diagnostics"))


def diagnostics_enabled() -> bool:
    return DIAGNOSTICS_ENABLED


def diagnostics_detail() -> str:
    return DIAGNOSTICS_DETAIL


def base_path() -> Path:
    return DIAGNOSTICS_BASE_PATH


def ensure_base_dir(sub_path: Iterable[str | Path]) -> Path:
    path = base_path().joinpath(*sub_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_json(relative_parts: Sequence[str | Path], data: dict) -> None:
    if not diagnostics_enabled():
        return
    path = ensure_base_dir(relative_parts)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def append_csv(relative_path: str | Path, headers: Sequence[str], row: Sequence) -> None:
    if not diagnostics_enabled():
        return
    path = base_path() / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        writer.writerow(row)


def append_text(relative_path: str | Path, lines: list[str]) -> None:
    if not diagnostics_enabled():
        return
    path = base_path() / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(f"{line}\n")


def allow_heavy_exports(year: int, active_bof_count: int | None = None) -> bool:
    if not diagnostics_enabled():
        return False
    if diagnostics_detail() == "full":
        return True
    if year >= 2048 and (active_bof_count is None or active_bof_count < 10 or year >= 2050):
        return True
    return False


def should_log_raw_bof_inputs(year: int, bof_count: int) -> bool:
    return allow_heavy_exports(year, bof_count)
