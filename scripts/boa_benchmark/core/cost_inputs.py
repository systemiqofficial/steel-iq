"""Runtime cost container for the benchmark: builds the flat, horizon-length arrays that
`boa_logic`/`boa_cost_calculations` expect, from the small `flat_costs.csv` table
produced once by `preprocessing/preprocess_costs.py`.

`cost_of_capital` is read per-region from `flat_costs.csv` (see `preprocessing/preprocess_costs.py`'s
`load_cost_of_capital`), not passed in -- so a region's WACC advantage/disadvantage (e.g.
China's comparatively low renewables WACC) actually shows up in the benchmark instead of
being masked by one global assumption. `investment_horizon` is reused from `boa_config`.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from baseload_optimisation_atlas.boa_config import LIFETIMES

INVESTMENT_HORIZON = max(LIFETIMES["solar"], LIFETIMES["wind"], LIFETIMES["battery"])


@dataclass
class BenchmarkCosts:
    region: str
    capex: dict[str, np.ndarray]  # "solar"/"wind", flat over investment_horizon+1, USD/MW
    opex_pct: dict[str, float]  # "solar"/"wind"/"battery"
    storage_costs: dict[str, np.ndarray]  # "battery_cost_per_installed_unit" (USD/MWh), "average_implied_storage"
    cost_of_capital: float
    investment_horizon: int = INVESTMENT_HORIZON


def load_benchmark_costs(flat_costs_csv: Path, region: str) -> BenchmarkCosts:
    df = pd.read_csv(flat_costs_csv)
    rows = df[df["region"] == region]
    if len(rows) != 1:
        available = ", ".join(sorted(df["region"]))
        raise ValueError(f"Region '{region}' not found (or ambiguous) in {flat_costs_csv}. Available: {available}")
    row = rows.iloc[0]

    horizon_len = INVESTMENT_HORIZON + 1
    capex = {
        "solar": np.full(horizon_len, row["capex_solar_usd_per_mw"]),
        "wind": np.full(horizon_len, row["capex_wind_usd_per_mw"]),
    }
    opex_pct = {
        "solar": float(row["opex_pct_solar"]),
        "wind": float(row["opex_pct_wind"]),
        "battery": float(row["opex_pct_battery"]),
    }
    storage_costs = {
        "battery_cost_per_installed_unit": np.full(horizon_len, row["battery_cost_usd_per_mwh"]),
        "average_implied_storage": np.full(horizon_len, row["avg_implied_storage"]),
    }

    return BenchmarkCosts(
        region=region,
        capex=capex,
        opex_pct=opex_pct,
        storage_costs=storage_costs,
        cost_of_capital=float(row["cost_of_capital"]),
    )
