"""
Energy-price trace: electricity and hydrogen prices from the site rasters and the country series
to what plants pay, checked against independent values.

``features.energy_trace`` in ``logging_config.yaml`` switches it on, and its ``energy_trace`` block
lists the countries and plants to follow. ``bootstrap_simulation`` calls ``configure`` once per run.
When the trace is off, every stage function returns before reading its arguments. When it is on, a
stage reads its arguments, never assigns to them and never draws a random number, so the trace
changes no price and no result.

Output: an INFO line ``[ENERGY TRACE] ...`` per traced entity and per stage summary, a WARNING
``[ENERGY CHECK] ...`` per failed check, and rows appended to ``<output_dir>/energy_trace.csv`` as
each stage runs, one per stage, year, entity and quantity.
"""

import csv
import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

import numpy as np
import pandas as pd

from steelo.domain.constants import MWH_TO_KWH, PERkWh_TO_PERMWh, T_TO_KG, Year
from steelo.utilities.variable_matching import POWER_MIX_TO_COVERAGE_MAP

if TYPE_CHECKING:
    import xarray as xr

    from steelo.adapters.repositories.interface import PlantRepository
    from steelo.domain.models import Environment, FurnaceGroup, HydrogenPriceInputs, Plant
    from steelo.domain.new_plant_opening import NewPlantLocation

logger = logging.getLogger(__name__)

CSV_NAME = "energy_trace.csv"
CSV_COLUMNS = (
    "year",
    "stage",
    "plant_id",
    "furnace_group_id",
    "iso3",
    "geo_key",
    "lat",
    "lon",
    "technology",
    "status",
    "new_site",
    "quantity",
    "value",
    "expected",
    "unit",
    "check_failed",
)
IDENTITY_COLUMNS = CSV_COLUMNS[2:11]
ALL_CELLS = "ALL"
REL_TOL = 1e-9
LCOE_REL_TOL = 1e-6  # the LCOE files hold float32 values
PER_MWH_DELIVERED = "per_mwh_delivered"
CARRIERS = ("electricity", "hydrogen")

FURNACE_GROUP_UNITS = {
    "electricity": "USD/kWh",
    "hydrogen": "USD/t",
    "electricity_paid": "USD/kWh",
    "hydrogen_paid": "USD/t",
}
UNITS: dict[str, dict[str, str]] = {
    "lcoe_source": {
        "file": "",
        "run": "",
        "boa_version": "",
        "lcoe_basis": "",
        "coverage_fraction": "fraction",
        "schema_version": "",
        "median_lcoe": "USD/MWh",
    },
    "site_power": {
        "coverage": "fraction",
        "median_grid_price": "USD/kWh",
        "median_lcoe": "USD/kWh",
        "median_power_price": "USD/kWh",
        "cells_checked": "cells",
        "cells_off_blend": "cells",
    },
    "site_hydrogen": {
        "median_lcoh": "USD/kg",
        "ceiling": "USD/kg",
        "median_capped_lcoh": "USD/kg",
        "share_capped": "fraction",
        "cells_checked": "cells",
        "cells_above_ceiling": "cells",
        "cells_nan_lcoh_priced": "cells",
    },
    "hydrogen_lcoe": {"lcoe": "USD/MWh", "from_neighbours": ""},
    "country_hydrogen": {
        "coverage": "fraction",
        "grid": "USD/kWh",
        "lcoe": "USD/kWh",
        "electricity": "USD/kWh",
        "lcoh": "USD/kg",
        "ceiling": "USD/kg",
        "capped": "USD/kg",
    },
    "brownfield": FURNACE_GROUP_UNITS,
    "greenfield_operating": FURNACE_GROUP_UNITS,
    "greenfield_select": {
        "site_electricity": "USD/kWh",
        "site_hydrogen": "USD/t",
        "country_electricity": "USD/kWh",
        "country_hydrogen": "USD/t",
        "override_reference_year": "year",
        "operating_start_year": "year",
    },
    "greenfield_revalue": {
        "electricity": "USD/kWh",
        "hydrogen": "USD/t",
        "site_power_price": "USD/kWh",
        "site_hydrogen": "USD/t",
    },
    "greenfield_build": {
        "electricity": "USD/kWh",
        "hydrogen": "USD/t",
        "reference_electricity": "USD/kWh",
        "reference_hydrogen": "USD/t",
        "regional_hydrogen": "USD/t",
        "operating_start_year": "year",
        "reference": "",
    },
}


@dataclass(frozen=True)
class _Check:
    """A checked quantity: the value the code holds, the independent expectation and the verdict."""

    value: float
    expected: float
    ok: bool


@dataclass
class _GreenfieldYear:
    """Counts the per-site and per-furnace-group stages gather until ``greenfield_summary``."""

    sites: int = 0
    site_prices_by_country: defaultdict[str, list[tuple[float, float]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    outside_ratio_by_country: Counter[str] = field(default_factory=Counter)
    revalue_checked: int = 0
    revalue_failed: int = 0
    revalue_skipped: int = 0
    built: int = 0
    built_without_reference: int = 0


@dataclass
class _State:
    """Everything the trace keeps for one run; ``configure`` replaces it whole."""

    enabled: bool = False
    countries: frozenset[str] = frozenset()
    plants: frozenset[str] = frozenset()
    csv_path: Path | None = None
    csv_started: bool = False
    site_prices: dict[str, dict[str, float]] = field(default_factory=dict)
    references: dict[str, dict[str, float]] = field(default_factory=dict)
    greenfield: _GreenfieldYear = field(default_factory=_GreenfieldYear)


_state = _State()


def configure(enabled: bool, countries: Iterable[str], plants: Iterable[str], output_dir: Path | None) -> None:
    """
    Switch the trace on or off for one run and reset everything it keeps.

    Args:
        enabled: Whether the trace runs (``features.energy_trace``).
        countries: ISO3 codes whose rasters are summarised and whose furnace groups are checked.
        plants: Plant IDs that get a row and a log line per active furnace group.
        output_dir: The run folder; ``energy_trace.csv`` is written there. None writes no CSV.

    Returns:
        None.

    Notes:
        Called by ``bootstrap_simulation`` once per run, so a second run in the same process starts
        with no rows, no construction references and a CSV of its own. The first row written
        replaces any ``energy_trace.csv`` already in the folder.
    """
    global _state
    _state = _State(
        enabled=enabled,
        countries=frozenset(countries),
        plants=frozenset(plants),
        csv_path=output_dir / CSV_NAME if enabled and output_dir is not None else None,
    )
    if enabled:
        logger.info(
            f"[ENERGY TRACE] on: {len(_state.countries)} countries, {len(_state.plants)} plants, "
            f"CSV {_state.csv_path if _state.csv_path is not None else 'not written (no output folder)'}"
        )


def enabled() -> bool:
    """
    Report whether the trace is on.

    Returns:
        True when the last ``configure`` switched the trace on.

    Notes:
        Stage functions check this first, before reading any argument.
    """
    return _state.enabled


def is_listed_plant(plant: "Plant") -> bool:
    """
    Report whether a plant is in the trace's plant list.

    Args:
        plant: Any plant.

    Returns:
        True when the plant's ID is listed.

    Notes:
        Listed plants get a CSV row and a log line per active furnace group, wherever they are.
    """
    return plant.plant_id in _state.plants


def in_listed_country(iso3: str) -> bool:
    """
    Report whether a country is in the trace's country list.

    Args:
        iso3: ISO3 code.

    Returns:
        True when the country is listed.

    Notes:
        Every active furnace group in a listed country is checked; it gets a row only when a check fails.
    """
    return iso3 in _state.countries


def is_new_site(plant: "Plant") -> bool:
    """
    Report whether a plant was opened by the model at a geospatial site.

    Args:
        plant: Any plant.

    Returns:
        True when the plant's ``parent_gem_id`` starts with ``indi``, the model's own new plants.

    Notes:
        Matches the test ``Environment.set_input_cost_in_furnace_groups`` uses to keep a new site's prices.
    """
    return plant.parent_gem_id.lower().startswith("indi")


def trace(stage: str, year: int, quantities: Mapping[str, object], *, log: bool = True, **identity: object) -> None:
    """
    Record unchecked quantities of one entity.

    Args:
        stage: Stage name, a key of ``UNITS``.
        year: Year the prices belong to.
        quantities: Quantity name to value; each name must be listed for the stage in ``UNITS``.
        log: Whether to write the INFO line; the CSV rows are written either way.
        **identity: The entity's CSV columns, any of ``IDENTITY_COLUMNS``.

    Returns:
        None.

    Notes:
        One INFO line for the entity and one CSV row per quantity.
    """
    if not _state.enabled:
        return
    _emit(stage, year, identity, quantities, {}, record=True, log=log)


def check(
    stage: str,
    year: int,
    quantity: str,
    ok: bool,
    expected: float,
    actual: float,
    *,
    record: bool = True,
    **identity: object,
) -> bool:
    """
    Record a checked quantity of one entity.

    Args:
        stage: Stage name, a key of ``UNITS``.
        year: Year the prices belong to.
        quantity: Quantity name, listed for the stage in ``UNITS``.
        ok: The verdict of the check.
        expected: The independent value the quantity should match or stay within.
        actual: The value the code holds.
        record: Whether a passed check writes its row and INFO line.
        **identity: The entity's CSV columns, any of ``IDENTITY_COLUMNS``.

    Returns:
        The verdict, ``ok``.

    Notes:
        A failed check always writes one WARNING and a row with ``check_failed`` 1. A passed check
        writes no WARNING.
    """
    if not _state.enabled:
        return ok
    _emit(stage, year, identity, {}, {quantity: _Check(actual, expected, ok)}, record=record, log=record)
    return ok


def lcoe_source(year: int, lcoe_file: Path, lcoe_ds: "xr.Dataset") -> None:
    """
    Trace which promoted LCOE file prices the run.

    Args:
        year: The run's start year.
        lcoe_file: The file bootstrap picked.
        lcoe_ds: The file, open and accepted.

    Returns:
        None.

    Notes:
        Rows for the file, its BOA run and version, basis, coverage and schema version. No check: a file
        that breaks the contract is refused before it gets here.
    """
    if not _state.enabled:
        return
    attrs = lcoe_ds.attrs
    values = {
        "file": str(lcoe_file),
        "run": attrs["run"] if "run" in attrs else "",
        "boa_version": attrs["boa_version"] if "boa_version" in attrs else "",
        "lcoe_basis": attrs["lcoe_basis"],
        "coverage_fraction": attrs["coverage_fraction"],
        "schema_version": attrs["schema_version"],
    }
    _emit("lcoe_source", year, {}, values, {}, record=True, log=True)


def lcoe_source_medians(ds: "xr.Dataset", year: int) -> None:
    """
    Trace each listed country's median baseload LCOE of the year.

    Args:
        ds: The global dataset with ``iso3`` and the year's ``lcoe`` (USD/kWh).
        year: Simulation year.

    Returns:
        None.

    Notes:
        Medians over the country's cells with an LCOE, in the file's USD/MWh.
    """
    if not _state.enabled:
        return
    dims = ds["lcoe"].dims
    lcoe, iso3 = (_raster(ds, name, dims) for name in ("lcoe", "iso3"))
    has_lcoe = np.isfinite(lcoe)
    for country, mask in _country_masks(iso3):
        if country == ALL_CELLS:
            continue
        median = _median(lcoe[mask & has_lcoe]) * PERkWh_TO_PERMWh
        _emit("lcoe_source", year, {"iso3": country}, {"median_lcoe": median}, {}, record=True, log=True)


def site_power(ds: "xr.Dataset", year: int, coverage: float, lcoe_basis: str | None) -> None:
    """
    Trace and check the site power price raster of the year.

    Args:
        ds: The global dataset with ``iso3``, ``grid_price``, ``lcoe`` and ``power_price`` (USD/kWh).
        year: Simulation year.
        coverage: Baseload coverage c, above zero.
        lcoe_basis: The LCOE file's basis: ``per_mwh_delivered``, or None for a cost per MWh of demand.

    Returns:
        None.

    Notes:
        Every cell with a finite ``power_price`` must equal ``(1 - c) × grid + c × lcoe`` on a
        delivered basis and ``(1 - c) × grid + lcoe`` otherwise. Rows per listed country and ``ALL``:
        the coverage, the medians and the count of cells off the blend.
    """
    if not _state.enabled:
        return
    stage = "site_power"
    dims = ds["power_price"].dims
    power, grid, lcoe, iso3 = (_raster(ds, name, dims) for name in ("power_price", "grid_price", "lcoe", "iso3"))
    expected = _expected_power_price(grid, lcoe, coverage, lcoe_basis)
    priced = np.isfinite(power)
    off_blend = priced & ~np.isclose(power, expected, rtol=REL_TOL, atol=0.0)
    for country, mask in _country_masks(iso3):
        cells = mask & priced
        off = int((mask & off_blend).sum())
        values = {
            "coverage": coverage,
            "median_grid_price": _median(grid[cells]),
            "median_lcoe": _median(lcoe[cells]),
            "median_power_price": _median(power[cells]),
            "cells_checked": int(cells.sum()),
        }
        checks = {"cells_off_blend": _Check(off, 0, off == 0)}
        _emit(stage, year, {"iso3": country}, values, checks, record=True, log=True)
    _summary(stage, year, f"checked {int(priced.sum())}, failed {int(off_blend.sum())}")


def site_hydrogen(ds: "xr.Dataset", year: int, regional_ceiling: Mapping[str, float]) -> None:
    """
    Trace and check the capped site hydrogen raster of the year.

    Args:
        ds: The global dataset with ``iso3``, ``tiam_ucl_region``, ``lcoh`` and ``capped_lcoh`` (USD/kg).
        year: Simulation year.
        regional_ceiling: Region to its hydrogen ceiling (USD/kg), before any trade option.

    Returns:
        None.

    Notes:
        Every cell with a finite ``capped_lcoh`` must stay within its region's ceiling. Cells with no
        LCOH that still carry a price are counted, not failed. With the ceiling percentile at 100 and
        intraregional trade off, as by default, the check cannot fail.
    """
    if not _state.enabled:
        return
    stage = "site_hydrogen"
    dims = ds["capped_lcoh"].dims
    lcoh, capped, regions, iso3 = (
        _raster(ds, name, dims) for name in ("lcoh", "capped_lcoh", "tiam_ucl_region", "iso3")
    )
    ceiling = pd.Series(regions.ravel()).map(regional_ceiling).to_numpy(dtype=float).reshape(regions.shape)
    priced = np.isfinite(capped)
    has_lcoh = np.isfinite(lcoh)
    with np.errstate(invalid="ignore"):
        above = priced & ~(capped <= ceiling + REL_TOL * np.abs(ceiling))
        capped_below_lcoh = priced & has_lcoh & (capped < lcoh)
    for country, mask in _country_masks(iso3):
        cells = mask & priced
        n_cells = int(cells.sum())
        n_above = int((mask & above).sum())
        values = {
            "median_lcoh": _median(lcoh[cells & has_lcoh]),
            "ceiling": _median(ceiling[cells]),
            "median_capped_lcoh": _median(capped[cells]),
            "share_capped": int((mask & capped_below_lcoh).sum()) / n_cells if n_cells else math.nan,
            "cells_checked": n_cells,
            "cells_nan_lcoh_priced": int((cells & ~has_lcoh).sum()),
        }
        checks = {"cells_above_ceiling": _Check(n_above, 0, n_above == 0)}
        _emit(stage, year, {"iso3": country}, values, checks, record=True, log=True)
    _summary(stage, year, f"checked {int(priced.sum())}, failed {int(above.sum())}")


def hydrogen_lcoe(
    year: int,
    lcoe: "xr.DataArray",
    iso3: "xr.DataArray",
    percentile: float,
    lcoe_by_geo_key: Mapping[str, float],
    neighbour_sources: Mapping[str, object],
) -> None:
    """
    Trace and check the baseload LCOE of every geo key for one priced year.

    Args:
        year: The year priced.
        lcoe: The year's LCOE grid read from the file (USD/MWh).
        iso3: The iso3 grid the geo keys were built on.
        percentile: The configured percentile of an area's cells (``hydrogen_lcoe_percentile``).
        lcoe_by_geo_key: Geo key to baseload LCOE (USD/kWh), as the hydrogen price reads it.
        neighbour_sources: The geo keys priced from their nearest cells, to their source cells.

    Returns:
        None.

    Notes:
        Rows for every geo key: ``lcoe`` in USD/MWh and ``from_neighbours``. Each listed country's own
        key not priced from neighbours must equal ``np.percentile`` at the configured percentile of the
        year's finite LCOE over the cells whose iso3 is the country. That value is computed here on the
        iso3 raster, apart from the geo key grid and the percentile code it checks, with the relative
        tolerance ``LCOE_REL_TOL``. Only listed countries get log lines. Runs at bootstrap, once per year.
    """
    if not _state.enabled:
        return
    stage = "hydrogen_lcoe"
    lcoe_values = lcoe.transpose(*iso3.dims).values
    finite = np.isfinite(lcoe_values)
    country_cells = {country: mask & finite for country, mask in _country_masks(iso3.values) if country != ALL_CELLS}
    checked = failed = 0
    for geo_key in sorted(lcoe_by_geo_key):
        country = geo_key.split(":", 1)[0]
        value = lcoe_by_geo_key[geo_key] * PERkWh_TO_PERMWh
        from_neighbours = geo_key in neighbour_sources
        values: dict[str, object] = {"from_neighbours": from_neighbours}
        checks: dict[str, _Check] = {}
        if geo_key in country_cells and not from_neighbours:
            cells = lcoe_values[country_cells[geo_key]].astype(float)
            expected = float(np.percentile(cells, percentile)) if cells.size else math.nan
            ok = math.isfinite(expected) and math.isclose(value, expected, rel_tol=LCOE_REL_TOL)
            checks["lcoe"] = _Check(value, expected, ok)
            checked += 1
            failed += not ok
        else:
            values["lcoe"] = value
        _emit(
            stage,
            year,
            {"iso3": country, "geo_key": geo_key},
            values,
            checks,
            record=True,
            log=country in _state.countries,
        )
    _summary(stage, year, f"checked {checked}, failed {failed}")


def country_hydrogen(
    year: int,
    power_mix: str,
    input_costs: Mapping[str, Mapping[Year, Mapping[str, float]]],
    baseload_lcoe_by_geo_key: Mapping[Year, Mapping[str, float]],
    hydrogen_efficiency: Mapping[Year, float],
    hydrogen_capex_opex: Mapping[str, Mapping[Year, float]],
    price_inputs: Mapping[str, "HydrogenPriceInputs"],
    lcoh_by_geo_key: Mapping[str, float],
    regional_ceilings: Mapping[str, float],
    country_to_region: Mapping[str, str],
    capped_prices: Mapping[str, float],
) -> None:
    """
    Trace and check the capped hydrogen price of every geo key for one priced year.

    Args:
        year: The year priced.
        power_mix: The run's power mix (``GeoConfig.included_power_mix``).
        input_costs: Geo key to year to carrier price, the master's input costs.
        baseload_lcoe_by_geo_key: Year to geo key to baseload LCOE (USD/kWh); empty without a baseload share.
        hydrogen_efficiency: Year to the electrolyser's electricity use (MWh/kg).
        hydrogen_capex_opex: Geo key to year to the electrolyser's capital and operating cost (USD/kg).
        price_inputs: Geo key to the inputs the price records, electricity included.
        lcoh_by_geo_key: Geo key to the LCOH (USD/kg) the cap was applied to.
        regional_ceilings: Region to ceiling (USD/kg).
        country_to_region: ISO3 to region; a province takes its country's region.
        capped_prices: Geo key to capped price (USD/kg).

    Returns:
        None.

    Notes:
        Three checks per geo key, from values computed here: the recorded ``electricity`` must equal
        ``(1 - c) × grid + c × LCOE``, with c from the power mix, the grid from the input costs and the
        LCOE from the series, or the grid alone without a baseload share; the ``lcoh`` the cap used must
        equal the electrolyser's kWh per kg × that electricity + its capital and operating cost, at the
        geo key else its country; and the capped price must stay within ``min(lcoh, region's ceiling)``,
        which holds with intraregional trade on or off. Every geo key gets rows; only listed countries
        get log lines. Runs at bootstrap, once per year of the precomputed series.
    """
    if not _state.enabled:
        return
    stage = "country_hydrogen"
    coverage = POWER_MIX_TO_COVERAGE_MAP[power_mix]
    checked = failed = 0
    for geo_key in sorted(capped_prices):
        iso3 = geo_key.split(":", 1)[0]
        record = price_inputs[geo_key]
        grid = _nested(input_costs, geo_key, year, "electricity")
        if coverage > 0:
            expected_electricity = (1 - coverage) * grid + coverage * _nested(baseload_lcoe_by_geo_key, year, geo_key)
        else:
            expected_electricity = grid
        capex_opex_key = geo_key if geo_key in hydrogen_capex_opex else iso3
        expected_lcoh = _nested(hydrogen_efficiency, year) * MWH_TO_KWH * expected_electricity + _nested(
            hydrogen_capex_opex, capex_opex_key, year
        )
        lcoh = lcoh_by_geo_key[geo_key]
        ceiling = regional_ceilings[country_to_region[iso3]]
        bound = min(lcoh, ceiling)
        capped = capped_prices[geo_key]
        values: dict[str, object] = {"coverage": record["coverage"], "grid": record["grid"]}
        if record["lcoe"] is not None:
            values["lcoe"] = record["lcoe"]
        values["ceiling"] = ceiling
        checks = {
            "electricity": _Check(
                record["electricity"],
                expected_electricity,
                _matches(record["electricity"], expected_electricity),
            ),
            "lcoh": _Check(lcoh, expected_lcoh, _matches(lcoh, expected_lcoh)),
            "capped": _Check(capped, bound, _at_most(capped, bound)),
        }
        checked += len(checks)
        failed += sum(not c.ok for c in checks.values())
        _emit(
            stage,
            year,
            {"iso3": iso3, "geo_key": geo_key},
            values,
            checks,
            record=True,
            log=iso3 in _state.countries,
        )
    _summary(stage, year, f"checked {checked}, failed {failed}")


def brownfield(
    plants: "PlantRepository",
    year: Year,
    input_costs: dict[str, dict[Year, dict[str, float]]],
    capped_hydrogen_costs: dict[str, float],
    active_statuses: Iterable[str],
) -> None:
    """
    Check the prices existing plants pay this year, before the trade model runs.

    Args:
        plants: The plant repository.
        year: Simulation year.
        input_costs: Geo key to year to carrier price, the master's input costs.
        capped_hydrogen_costs: Geo key to capped hydrogen price of the year (USD/kg).
        active_statuses: Statuses that count as operating.

    Returns:
        None.

    Notes:
        For every active furnace group of a listed plant or of an existing plant in a listed country:
        no-subsidy electricity must equal the geo key's input cost for the year, and no-subsidy
        hydrogen the geo key's capped price × ``T_TO_KG``. Listed plants get rows and log lines with
        the subsidised prices too; other furnace groups only when a check fails.
    """
    if not _state.enabled:
        return
    stage = "brownfield"
    active = set(active_statuses)
    checked = failed = 0
    for plant in plants.list():
        listed = is_listed_plant(plant)
        if is_new_site(plant) or not (listed or in_listed_country(plant.location.iso3)):
            continue
        year_costs = plant.location.resolve(input_costs, what="input costs")
        expected = {
            "electricity": _price(year_costs[year], "electricity") if year_costs is not None else math.nan,
            "hydrogen": _resolved_hydrogen(plant, capped_hydrogen_costs),
        }
        for fg in plant.furnace_groups:
            if fg.status.lower() not in active:
                continue
            checks: dict[str, _Check] = {}
            for carrier in CARRIERS:
                actual = _price(fg.energy_costs_no_subsidy, carrier)
                checks[carrier] = _Check(actual, expected[carrier], _matches(actual, expected[carrier]))
            checked += len(checks)
            failed += sum(not c.ok for c in checks.values())
            _emit(stage, year, _furnace_group_identity(plant, fg), _paid(fg), checks, record=listed, log=listed)
    _summary(stage, year, f"checked {checked}, failed {failed}")


def greenfield_operating(plants: "PlantRepository", year: int, active_statuses: Iterable[str]) -> None:
    """
    Check the prices operating new sites pay this year, before the trade model runs.

    Args:
        plants: The plant repository.
        year: Simulation year.
        active_statuses: Statuses that count as operating.

    Returns:
        None.

    Notes:
        No-subsidy electricity and hydrogen must equal the references frozen when the site entered
        construction. A furnace group without a reference is traced unchecked and counted as skipped.
    """
    if not _state.enabled:
        return
    stage = "greenfield_operating"
    active = set(active_statuses)
    checked = failed = skipped = 0
    for plant in plants.list():
        if not is_new_site(plant):
            continue
        for fg in plant.furnace_groups:
            if fg.status.lower() not in active:
                continue
            reference = _state.references.get(fg.furnace_group_id, {})
            values: dict[str, object] = {}
            checks: dict[str, _Check] = {}
            for carrier in CARRIERS:
                actual = _price(fg.energy_costs_no_subsidy, carrier)
                if carrier in reference:
                    checks[carrier] = _Check(actual, reference[carrier], _matches(actual, reference[carrier]))
                else:
                    values[carrier] = actual
            values.update(_paid(fg))
            checked += len(checks)
            failed += sum(not c.ok for c in checks.values())
            skipped += len(CARRIERS) - len(checks)
            _emit(stage, year, _furnace_group_identity(plant, fg), values, checks, record=True, log=True)
    _summary(stage, year, f"checked {checked}, failed {failed}, skipped {skipped} (no reference)")


def greenfield_select(
    year: Year,
    site: "NewPlantLocation",
    geo_key: str,
    site_costs: Mapping[str, float],
    country_costs: Mapping[Year, Mapping[str, float]],
    target_year: int,
    construction_time: int,
) -> None:
    """
    Trace a candidate site's prices as the NPV at selection receives them.

    Args:
        year: Simulation year, which is also the NPV overrides' reference year.
        site: The candidate site with its raster prices.
        geo_key: The site's geo key.
        site_costs: The site's energy costs after the site prices replaced the country ones.
        country_costs: Year to carrier price for the site's geo key, the master's input costs.
        target_year: The year the opportunity would start construction.
        construction_time: Years of construction.

    Returns:
        None.

    Notes:
        One row set per site, no log line; ``greenfield_summary`` logs the per-country summaries.
    """
    if not _state.enabled:
        return
    stage = "greenfield_select"
    iso3 = site["iso3"]
    country_year = country_costs[year]
    site_electricity = site_costs["electricity"]
    site_hydrogen = site_costs["hydrogen"]
    country_electricity = _price(country_year, "electricity")
    values = {
        "site_electricity": site_electricity,
        "site_hydrogen": site_hydrogen,
        "country_electricity": country_electricity,
        "country_hydrogen": _price(country_year, "hydrogen"),
        "override_reference_year": year,
        "operating_start_year": target_year + construction_time,
    }
    identity = {"iso3": iso3, "geo_key": geo_key, "lat": site["Latitude"], "lon": site["Longitude"], "new_site": True}
    _emit(stage, year, identity, values, {}, record=True, log=False)
    tally = _state.greenfield
    tally.sites += 1
    if iso3 in _state.countries:
        tally.site_prices_by_country[iso3].append((site_electricity, site_hydrogen))
        ratio = site_electricity / country_electricity if country_electricity != 0 else math.inf
        tally.outside_ratio_by_country[iso3] += not 0.1 <= ratio <= 10


def greenfield_revalue(
    plant: "Plant",
    fg: "FurnaceGroup",
    year: int,
    site_power_price: "np.ndarray",
    site_capped_lcoh: "np.ndarray",
    written: Mapping[str, float],
) -> None:
    """
    Check the prices a considered or announced new site is re-valued with this year.

    Args:
        plant: The new site's plant.
        fg: The opportunity's furnace group.
        year: Simulation year.
        site_power_price: The site's ``power_price`` raster value (USD/kWh).
        site_capped_lcoh: The site's ``capped_lcoh`` raster value (USD/kg).
        written: The no-subsidy energy costs the re-valuation writes.

    Returns:
        None.

    Notes:
        The written hydrogen must equal the site's ``capped_lcoh`` × ``T_TO_KG``; a site with no
        raster value gets its row without the check. The finite raster prices are kept as the
        furnace group's candidate references, which ``greenfield_build`` freezes.
    """
    if not _state.enabled:
        return
    stage = "greenfield_revalue"
    site_power = float(site_power_price)
    site_hydrogen = float(site_capped_lcoh) * T_TO_KG
    kept = _state.site_prices.setdefault(fg.furnace_group_id, {})
    if math.isfinite(site_power):
        kept["electricity"] = site_power
    if math.isfinite(site_hydrogen):
        kept["hydrogen"] = site_hydrogen
    hydrogen = _price(written, "hydrogen")
    values: dict[str, object] = {
        "electricity": _price(written, "electricity"),
        "site_power_price": site_power,
        "site_hydrogen": site_hydrogen,
    }
    checks: dict[str, _Check] = {}
    tally = _state.greenfield
    if math.isfinite(site_hydrogen):
        checks["hydrogen"] = _Check(hydrogen, site_hydrogen, _matches(hydrogen, site_hydrogen))
        tally.revalue_checked += 1
        tally.revalue_failed += not checks["hydrogen"].ok
    else:
        values["hydrogen"] = hydrogen
        tally.revalue_skipped += 1
    _emit(stage, year, _furnace_group_identity(plant, fg), values, checks, record=True, log=True)


def greenfield_build(plant: "Plant", fg: "FurnaceGroup", year: Year, env: "Environment") -> None:
    """
    Trace a new site entering construction and freeze its reference prices.

    Args:
        plant: The new site's plant.
        fg: The furnace group entering construction, its operating start year already set.
        year: Construction start year.
        env: The environment, for the geo key's capped hydrogen price of the year.

    Returns:
        None.

    Notes:
        The reference is the site's raster prices from the furnace group's last re-valuation, which
        ``greenfield_operating`` expects once the site operates. A furnace group never re-valued
        gets a ``reference`` row reading ``missing``. ``regional_hydrogen`` is recorded for reading;
        no check uses it.
    """
    if not _state.enabled:
        return
    stage = "greenfield_build"
    regional = _resolved_hydrogen(plant, env.capped_hydrogen_costs_for_year(year))
    values: dict[str, object] = {carrier: _price(fg.energy_costs_no_subsidy, carrier) for carrier in CARRIERS}
    reference = dict(_state.site_prices.get(fg.furnace_group_id, {}))
    for carrier, price in reference.items():
        values[f"reference_{carrier}"] = price
    if not reference:
        values["reference"] = "missing"
    values["regional_hydrogen"] = regional
    values["operating_start_year"] = fg.lifetime.time_frame.start
    _state.references[fg.furnace_group_id] = reference
    _state.greenfield.built += 1
    _state.greenfield.built_without_reference += not reference
    _emit(stage, year, _furnace_group_identity(plant, fg), values, {}, record=True, log=True)


def greenfield_summary(year: int) -> None:
    """
    Log the year's summary lines of the per-site and per-furnace-group new-site stages.

    Args:
        year: Simulation year.

    Returns:
        None.

    Notes:
        For ``greenfield_select``: per listed country the candidate sites, the median site prices
        and the number whose power price is outside 0.1 to 10 times the country's, then the total.
        For ``greenfield_revalue`` the checks; for ``greenfield_build`` the sites built. Resets the
        counts for the next year.
    """
    if not _state.enabled:
        return
    tally = _state.greenfield
    for iso3 in sorted(tally.site_prices_by_country):
        prices = np.array(tally.site_prices_by_country[iso3])
        _summary(
            "greenfield_select",
            year,
            f"iso3={iso3} sites={len(prices)} "
            f"median_site_electricity={_fmt(_median(prices[:, 0]))} USD/kWh "
            f"median_site_hydrogen={_fmt(_median(prices[:, 1]))} USD/t "
            f"outside_ratio={tally.outside_ratio_by_country[iso3]}",
        )
    _summary("greenfield_select", year, f"sites {tally.sites}")
    _summary(
        "greenfield_revalue",
        year,
        f"checked {tally.revalue_checked}, failed {tally.revalue_failed}, skipped {tally.revalue_skipped} (no site value)",
    )
    _summary("greenfield_build", year, f"built {tally.built}, without reference {tally.built_without_reference}")
    _state.greenfield = _GreenfieldYear()


def _expected_power_price(grid: np.ndarray, lcoe: np.ndarray, coverage: float, lcoe_basis: str | None) -> np.ndarray:
    """
    The power price each cell should hold, from the LCOE file's basis.

    Args:
        grid: Grid price per cell (USD/kWh).
        lcoe: Baseload LCOE per cell (USD/kWh).
        coverage: Baseload coverage c.
        lcoe_basis: ``per_mwh_delivered``, or None for a cost per MWh of demand.

    Returns:
        ``(1 - c) × grid + c × lcoe`` on a delivered basis, else ``(1 - c) × grid + lcoe``.

    Notes:
        Written from the file's declared basis, not from the blend line it checks.
    """
    if lcoe_basis == PER_MWH_DELIVERED:
        return (1 - coverage) * grid + coverage * lcoe
    if lcoe_basis is None:
        return grid * (1 - coverage) + lcoe
    raise ValueError(f"Unknown LCOE basis {lcoe_basis!r}")


def _raster(ds: "xr.Dataset", name: str, dims: tuple) -> np.ndarray:
    """
    A raster's values in a given dimension order.

    Args:
        ds: The global dataset.
        name: Variable name.
        dims: The dimension order to read in.

    Returns:
        The values, aligned cell by cell with every other raster read in the same order.

    Notes:
        A merged LCOE file keeps its own dimension order, which need not match the computed rasters'.
    """
    return ds[name].transpose(*dims).values


def _country_masks(iso3_values: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """
    Cell masks for each listed country, then for every cell.

    Args:
        iso3_values: The ``iso3`` raster.

    Returns:
        (ISO3, mask) per listed country in order, then (``ALL``, all cells).
    """
    flat = pd.Series(iso3_values.ravel())
    masks = [(country, (flat == country).to_numpy().reshape(iso3_values.shape)) for country in sorted(_state.countries)]
    masks.append((ALL_CELLS, np.ones(iso3_values.shape, dtype=bool)))
    return masks


def _median(values: np.ndarray) -> float:
    """
    Median of an array, NaN when it is empty.

    Args:
        values: Finite values.

    Returns:
        The median as a float.
    """
    return float(np.median(values)) if values.size else math.nan


def _price(costs: Mapping[str, float], carrier: str) -> float:
    """
    A carrier's price, NaN when the carrier is missing.

    Args:
        costs: Carrier to price.
        carrier: Carrier name.

    Returns:
        The price, or NaN.

    Notes:
        A missing price must fail the check that reads it, not stop the run.
    """
    return float(costs[carrier]) if carrier in costs else math.nan


def _nested(mapping: Mapping[Any, Any], *keys: object) -> float:
    """
    A value from nested mappings, NaN when a key is missing.

    Args:
        mapping: The outer mapping.
        *keys: One key per level, outermost first.

    Returns:
        The value as a float, or NaN.

    Notes:
        A missing input must fail the check that reads it, not stop the run.
    """
    value: Any = mapping
    for key in keys:
        if key not in value:
            return math.nan
        value = value[key]
    return float(value)


def _resolved_hydrogen(plant: "Plant", capped_hydrogen_costs: dict[str, float]) -> float:
    """
    The capped hydrogen price of a plant's geo key, in USD/t.

    Args:
        plant: Any plant.
        capped_hydrogen_costs: Geo key to capped price (USD/kg).

    Returns:
        The finest-available price × ``T_TO_KG``, or NaN when the geo key has none.
    """
    price = plant.location.resolve(capped_hydrogen_costs, what="hydrogen price")
    return price * T_TO_KG if price is not None else math.nan


def _matches(actual: float, expected: float) -> bool:
    """
    Whether two prices are equal within the relative tolerance.

    Args:
        actual: The value the code holds.
        expected: The independent value.

    Returns:
        True when both are finite and equal within ``REL_TOL``.
    """
    return math.isfinite(actual) and math.isfinite(expected) and math.isclose(actual, expected, rel_tol=REL_TOL)


def _at_most(actual: float, bound: float) -> bool:
    """
    Whether a price stays within a bound, with the relative tolerance.

    Args:
        actual: The value the code holds.
        bound: The upper bound.

    Returns:
        True when both are finite and ``actual`` exceeds ``bound`` by no more than ``REL_TOL``.
    """
    return math.isfinite(actual) and math.isfinite(bound) and actual <= bound + REL_TOL * abs(bound)


def _paid(fg: "FurnaceGroup") -> dict[str, object]:
    """
    The subsidised electricity and hydrogen prices a furnace group pays.

    Args:
        fg: Any furnace group.

    Returns:
        ``electricity_paid`` and ``hydrogen_paid``, NaN where the carrier is missing.
    """
    return {f"{carrier}_paid": _price(fg.energy_costs, carrier) for carrier in CARRIERS}


def _furnace_group_identity(plant: "Plant", fg: "FurnaceGroup") -> dict[str, object]:
    """
    The CSV identity columns of a furnace group.

    Args:
        plant: The furnace group's plant.
        fg: The furnace group.

    Returns:
        Plant, furnace group, geography, position, technology, status and whether it is a new site.
    """
    return {
        "plant_id": plant.plant_id,
        "furnace_group_id": fg.furnace_group_id,
        "iso3": plant.location.iso3,
        "geo_key": plant.location.geo_key,
        "lat": plant.location.lat,
        "lon": plant.location.lon,
        "technology": fg.technology.name,
        "status": fg.status,
        "new_site": is_new_site(plant),
    }


def _emit(
    stage: str,
    year: int,
    identity: Mapping[str, object],
    values: Mapping[str, object],
    checks: Mapping[str, _Check],
    *,
    record: bool,
    log: bool,
) -> None:
    """
    Write one entity's rows and lines.

    Args:
        stage: Stage name, a key of ``UNITS``.
        year: Year the prices belong to.
        identity: The entity's CSV columns.
        values: Unchecked quantities.
        checks: Checked quantities.
        record: Whether to write rows for unchecked quantities and passed checks.
        log: Whether to write the entity's INFO line.

    Returns:
        None.

    Notes:
        A failed check always writes its row and a WARNING. Unknown identity columns, stages or
        quantity names raise: they are programming errors.
    """
    unknown = set(identity) - set(IDENTITY_COLUMNS)
    if unknown:
        raise ValueError(f"Unknown energy trace columns {sorted(unknown)}")
    units = UNITS[stage]
    rows: list[list[object]] = []
    if record:
        rows.extend(_row(stage, year, identity, quantity, value, None, False) for quantity, value in values.items())
    for quantity, result in checks.items():
        if record or not result.ok:
            rows.append(_row(stage, year, identity, quantity, result.value, result.expected, not result.ok))
        if not result.ok:
            logger.warning(
                f"[ENERGY CHECK] {_describe(stage, year, identity)} "
                f"{quantity}={_fmt(result.value)} {units[quantity]} expected {_fmt(result.expected)}"
            )
    if log and logger.isEnabledFor(logging.INFO):
        parts = [f"{quantity}={_fmt(value)} {units[quantity]}".rstrip() for quantity, value in values.items()]
        parts += [
            f"{quantity}={_fmt(result.value)} {units[quantity]} (expected {_fmt(result.expected)})"
            for quantity, result in checks.items()
        ]
        logger.info(f"[ENERGY TRACE] {_describe(stage, year, identity)} {' '.join(parts)}")
    _write_rows(rows)


def _row(
    stage: str,
    year: int,
    identity: Mapping[str, object],
    quantity: str,
    value: object,
    expected: object,
    failed: bool,
) -> list[object]:
    """
    One CSV row in ``CSV_COLUMNS`` order.

    Args:
        stage: Stage name.
        year: Year the prices belong to.
        identity: The entity's CSV columns; absent ones stay empty.
        quantity: Quantity name, listed for the stage in ``UNITS``.
        value: The quantity's value.
        expected: The independent value, None for an unchecked quantity.
        failed: Whether a check failed.

    Returns:
        The row's cells.
    """
    cells = [identity[column] if column in identity else None for column in IDENTITY_COLUMNS]
    row = [year, stage, *cells, quantity, value, expected, UNITS[stage][quantity], failed]
    return [_cell(item) for item in row]


def _cell(item: object) -> object:
    """
    A value as the CSV writes it.

    Args:
        item: Any row value.

    Returns:
        An empty string for None, 1 or 0 for a boolean, a plain Python number for a NumPy scalar,
        else the value itself.
    """
    if item is None:
        return ""
    if isinstance(item, (bool, np.bool_)):
        return int(item)
    if isinstance(item, np.generic):
        return item.item()
    return item


def _write_rows(rows: list[list[object]]) -> None:
    """
    Append rows to the run's CSV, writing the header first.

    Args:
        rows: Rows in ``CSV_COLUMNS`` order.

    Returns:
        None.

    Notes:
        Writes nothing without an output folder. The first write of a run replaces an old file.
    """
    if _state.csv_path is None or not rows:
        return
    with _state.csv_path.open("a" if _state.csv_started else "w", newline="") as f:
        writer = csv.writer(f)
        if not _state.csv_started:
            writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)
    _state.csv_started = True


def _describe(stage: str, year: int, identity: Mapping[str, object]) -> str:
    """
    The stage, year and identity part of a log line.

    Args:
        stage: Stage name.
        year: Year the prices belong to.
        identity: The entity's CSV columns.

    Returns:
        ``stage=<stage> year=<year>`` followed by ``column=value`` for each identity column given.
    """
    parts = [f"stage={stage}", f"year={year}"]
    parts += [f"{column}={_fmt(identity[column])}" for column in IDENTITY_COLUMNS if column in identity]
    return " ".join(parts)


def _summary(stage: str, year: int, text: str) -> None:
    """
    Log one summary line of a stage.

    Args:
        stage: Stage name.
        year: Year the prices belong to.
        text: The summary, e.g. ``checked n, failed m``.

    Returns:
        None.
    """
    logger.info(f"[ENERGY TRACE] stage={stage} year={year} {text}")


def _fmt(value: object) -> str:
    """
    A value as a log line shows it.

    Args:
        value: Any value.

    Returns:
        Floats to ten significant digits, booleans as 1 or 0, anything else as text.
    """
    if isinstance(value, (bool, np.bool_)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        return f"{value:.10g}"
    return str(value)
