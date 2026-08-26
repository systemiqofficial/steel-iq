"""
Promote a run's per-year GLOBAL optimal-solution NetCDFs into one combined LCOE file.

The steel simulation reads exactly one variable off a BOA run — ``lcoe`` — but a
multi-year run spreads it over one ~150 MB file per year (5.49 GB for 36 years),
each repeating a grid of cost keys and status codes that never change between
years. Promotion stacks the LCOE into a single ``(year, lat, lon)`` float32
variable and stores ``cost_key``/``status`` once, which is ~26 MB for the same
36 years.

The per-year files stay where they are: they carry the overbuild factors and the
cost breakdown, and remain the artefacts for crash recovery, plots and forensics.

Provenance travels inside the promoted file — run name, input/cost sets, workbook
hash, versions and scenario settings — so the file alone identifies what produced it.
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xarray as xr

from boa.config import run_manifest
from boa.config.constants import STATUS_CODES
from boa.config.paths import PathConfig

# Scenario attributes copied from the per-year files; all are year-independent, so a
# disagreement between years means the directory mixes runs and promotion must stop.
CARRIED_ATTRS = (
    "investment_horizon_years",
    "baseload_demand_mw",
    "coverage_fraction",
    "p_percentile",
    "n_samples",
    "random_seed",
    "min_survivor_fraction",
    "min_survivors",
    "era5_weather_year",
    "era5_resolution_deg",
)


def discover_scenarios(path_config: PathConfig) -> list[tuple[float, int]]:
    """Every ``(baseload demand, p)`` in the run whose GLOBAL directory holds year files."""
    found: set[tuple[float, int]] = set()
    for global_dir in path_config.outputs_dir.glob("*MW/p*/nc/GLOBAL"):
        demand_dir, p_dir = global_dir.parents[2], global_dir.parents[1]
        try:
            scenario = (float(demand_dir.name.removesuffix("MW")), int(p_dir.name.removeprefix("p")))
        except ValueError:
            continue
        if any(global_dir.glob(path_config.optimal_sol_year_glob(*scenario, "GLOBAL"))):
            found.add(scenario)
    return sorted(found)


def year_files(path_config: PathConfig, baseload_demand: float, p: int) -> dict[int, Path]:
    """The scenario's GLOBAL per-year NetCDFs, keyed by investment year, in year order."""
    global_dir = path_config.maps_dir(baseload_demand, p, "GLOBAL")
    files: dict[int, Path] = {}
    for f in global_dir.glob(path_config.optimal_sol_year_glob(baseload_demand, p, "GLOBAL")):
        try:
            files[int(f.stem.rsplit("_", 1)[1])] = f
        except ValueError:
            logging.warning(f"Ignoring {f.name}: no investment year in the filename.")
    return dict(sorted(files.items()))


def _encode_cost_keys(cost_key: np.ndarray) -> tuple[np.ndarray, str]:
    """Map the per-pixel cost-key strings onto int16 ids plus a comma-joined legend."""
    legend_values, inverse = np.unique(cost_key.ravel(), return_inverse=True)
    legend = [str(k) for k in legend_values]
    if any("," in key for key in legend):
        raise ValueError(f"Cost keys contain a comma, which the legend uses as its separator: {legend}")
    if len(legend) > np.iinfo(np.int16).max:
        raise ValueError(f"{len(legend)} distinct cost keys exceed the int16 id range.")
    return inverse.reshape(cost_key.shape).astype(np.int16), ",".join(legend)


def _provenance_attrs(path_config: PathConfig, reference: xr.Dataset, years: list[int]) -> dict:
    """Everything needed to identify the run, read off the run manifest and the per-year files."""
    manifest = run_manifest.load(path_config) or {}
    provenance = manifest.get("provenance", {})
    invocations = manifest.get("invocations", [])
    attrs = {name: reference.attrs[name] for name in CARRIED_ATTRS if name in reference.attrs}
    attrs.update(
        {
            "source": "Baseload Optimisation Atlas (BOA)",
            "title": "Optimal-system LCOE per investment year, for the steel-iq simulation",
            "run": path_config.run,
            "input_set": provenance.get("input_set", path_config.input_set),
            "cost_set": provenance.get("cost_set", path_config.cost_set),
            "input_data_sha256": provenance.get("input_data_sha256") or "",
            "boa_version": provenance.get("boa_version", ""),
            "run_created_at": manifest.get("created_at", ""),
            "run_git_sha": (invocations[-1].get("git_sha") if invocations else None) or "",
            "promoted_years": np.asarray(years, dtype="int32"),
            "promoted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    return attrs


def promote_lcoe(path_config: PathConfig, baseload_demand: float, p: int) -> Path:
    """
    Combine one scenario's per-year GLOBAL NetCDFs into a single LCOE file and return its path.

    Raises if the years disagree on the grid, on ``cost_key``/``status`` or on any
    scenario attribute — all three are stored once, so year-invariance is a contract
    rather than an assumption.
    """
    files = year_files(path_config, baseload_demand, p)
    if not files:
        raise FileNotFoundError(
            f"No GLOBAL optimal-solution NetCDFs for {baseload_demand:g} MW p{p} in "
            f"{path_config.maps_dir(baseload_demand, p, 'GLOBAL')} — run `boa-run` first."
        )
    years = list(files)
    t0 = time.time()
    logging.info(f"Promoting {baseload_demand:g} MW p{p}: {len(years)} years ({years[0]}-{years[-1]}).")

    with xr.open_dataset(files[years[0]]) as reference:
        lat, lon = reference["lat"].values, reference["lon"].values
        cost_key = np.asarray(reference["cost_key"].values)
        status = np.asarray(reference["status"].values).astype(np.int8)
        attrs = _provenance_attrs(path_config, reference, years)

    lcoe = np.empty((len(years), lat.size, lon.size), dtype=np.float32)
    bytes_in = 0
    for i, year in enumerate(years):
        bytes_in += files[year].stat().st_size
        with xr.open_dataset(files[year]) as ds:
            if not (np.array_equal(ds["lat"].values, lat) and np.array_equal(ds["lon"].values, lon)):
                raise ValueError(f"{files[year].name} is on a different grid than {files[years[0]].name}.")
            if i and not np.array_equal(np.asarray(ds["cost_key"].values), cost_key):
                raise ValueError(f"cost_key differs between {files[years[0]].name} and {files[year].name}.")
            if i and not np.array_equal(np.asarray(ds["status"].values).astype(np.int8), status):
                raise ValueError(f"status differs between {files[years[0]].name} and {files[year].name}.")
            differing = {k: (attrs[k], ds.attrs[k]) for k in CARRIED_ATTRS if k in attrs and ds.attrs[k] != attrs[k]}
            if differing:
                raise ValueError(f"{files[year].name} was produced with different settings: {differing}.")
            lcoe[i] = ds["lcoe"].values.astype(np.float32)

    cost_key_id, legend = _encode_cost_keys(cost_key)
    attrs["cost_key_legend"] = legend
    # Semicolons, not commas: one status label contains a comma.
    attrs["status_legend"] = ";".join(f"{code}={label}" for code, label in STATUS_CODES.items())

    promoted = xr.Dataset(
        coords={"year": np.asarray(years, dtype="int32"), "lat": lat, "lon": lon},
        data_vars={
            "lcoe": (("year", "lat", "lon"), lcoe, {"units": "USD/MWh"}),
            "cost_key_id": (("lat", "lon"), cost_key_id, {"description": "Index into the cost_key_legend attribute"}),
            "status": (("lat", "lon"), status, {"description": "Index into the status_legend attribute"}),
        },
        attrs=attrs,
    )
    # One chunk per year keeps a single-year read off the other years' bytes.
    encoding = {
        "lcoe": {"dtype": "float32", "zlib": True, "complevel": 4, "chunksizes": (1, lat.size, lon.size)},
        "cost_key_id": {"dtype": "int16", "zlib": True, "complevel": 4},
        "status": {"dtype": "int8", "zlib": True, "complevel": 4},
    }

    output_path = path_config.promoted_lcoe_path(baseload_demand, p, years[0], years[-1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    promoted.to_netcdf(output_path, mode="w", format="NETCDF4", encoding=encoding)
    logging.info(
        f"Promoted {bytes_in / 1e6:.0f} MB of per-year files into {output_path.stat().st_size / 1e6:.1f} MB "
        f"at {output_path} in {time.time() - t0:.1f}s."
    )
    return output_path


def promote_all(path_config: PathConfig) -> list[Path]:
    """Promote every scenario found in the run; raises if the run holds no GLOBAL outputs."""
    scenarios = discover_scenarios(path_config)
    if not scenarios:
        raise FileNotFoundError(
            f"Run '{path_config.run}' has no GLOBAL optimal-solution NetCDFs under {path_config.outputs_dir}."
        )
    return [promote_lcoe(path_config, baseload_demand, p) for baseload_demand, p in scenarios]
