import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SET = "default"

# Prefix of the weather half of an input-set name, as `default_input_set` composes it.
WEATHER_SET_PREFIX = "cds-"


def weather_set_name(weather_year: int) -> str:
    """
    The weather half of an input-set name: ``cds-<year>``.

    Composed from the year, never split back out of ``input_set``. That name reaches us from
    ``--inputs`` and is user-supplied, and an availability tag containing a hyphen would make
    any split silently mis-key whatever was derived from it.
    """
    return f"{WEATHER_SET_PREFIX}{int(weather_year)}"


def format_scenario_number(value: float, *, pad_int_digits: int = 1) -> str:
    """
    Format a coverage/load_density float for a path/filename component: dot-free (``.`` ->
    ``p``, e.g. ``0.85`` -> ``"0p85"``, ``1.0`` -> ``"1"``) and with the integer part
    zero-padded to at least ``pad_int_digits``.

    Dot-free keeps a literal ``.`` out of a directory/filename component that naive
    ``Path.stem``/``.suffix`` code could later misparse as an extension. Zero-padding
    (``load_density`` uses ``pad_int_digits=2``) keeps a plain ``ls``/glob lexical sort in
    true numeric order for multi-digit values (``02MWkm2`` before ``10MWkm2``). Both
    properties are lossless and collision-free, unlike a percent-rounding scheme (``0.995``
    and ``1.0`` would otherwise both round to the same token).

    The inverse is ``parse_scenario_number``.
    """
    text = f"{value:g}"
    sign, text = ("-", text[1:]) if text.startswith("-") else ("", text)
    int_part, _, frac_part = text.partition(".")
    int_part = int_part.zfill(pad_int_digits)
    return f"{sign}{int_part}p{frac_part}" if frac_part else f"{sign}{int_part}"


def parse_scenario_number(token: str) -> float:
    """Inverse of ``format_scenario_number``: a ``p``-separated, possibly zero-padded token
    back into a float. Leading zeros in the integer part are handled by ``float`` itself."""
    return float(token.replace("p", ".", 1))


def make_run_dirname(run: str, params_hash: str) -> str:
    """``<run>_<params_hash>`` — the on-disk run directory name.

    The hash (see ``run_manifest.non_scenario_params_hash``) makes the fork decision
    automatic: the same ``run`` label with the same non-scenario parameters always
    resolves to the same directory, and a change to those parameters always resolves to a
    different one -- no manifest scan or description-matching needed.
    """
    return f"{run}_{params_hash}"


def resolve_run_dir(root: Path, run: str) -> Path:
    """
    Resolve a run label to its on-disk directory under ``<root>/runs/``.

    ``boa-run`` always names a run's directory ``<label>_<hash>`` (``make_run_dirname``), so a
    caller that only knows the label a user typed -- ``boa-promote-lcoe --run <label>``, for
    instance -- cannot find it with a literal path join. This globs ``<label>_*`` for that case,
    while still accepting the full hashed name unchanged so a caller that already has it (e.g.
    from a manifest or a previous listing) is not forced to guess the label back out of it.

    Raises if the label resolves to zero or more than one directory, naming what was found
    either way rather than silently picking one.
    """
    runs_dir = root / "runs"
    exact = runs_dir / run
    if exact.is_dir():
        return exact
    matches = sorted(p for p in runs_dir.glob(f"{run}_*") if p.is_dir()) if runs_dir.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"No run directory matches '{run}' under {runs_dir}.")
    raise ValueError(
        f"Run label '{run}' matches multiple directories under {runs_dir}: "
        f"{[p.name for p in matches]}; pass the full directory name to disambiguate."
    )


def default_root() -> Path:
    """Resolve the BOA data root: ``$BOA_DATA_ROOT``, else ``$STEELO_HOME/boa``, else ``~/.steelo/boa``.

    Mirrors steelo's ``STEELO_HOME`` convention without importing steelo, so the package stays standalone.
    """
    if (root := os.getenv("BOA_DATA_ROOT")) is not None:
        return Path(root)
    if (home := os.getenv("STEELO_HOME")) is not None:
        return Path(home) / "boa"
    return Path.home() / ".steelo" / "boa"


@dataclass
class PathConfig:
    """Paths for the baseload power simulation.

    Everything lives under one root, split by what it is derived from so that inputs can be swapped without silently
    reusing a stale cache:

        <root>/
        ├── data/                          single slot: shapefiles, lsm, iso3 grid, cds/ raw NetCDFs,
        │                                  lulc/ land-cover raster
        ├── inputs/<input_set>/            profile + max-capacity stores (cds-zarr/, atlite/)
        │   └── staging/                   freshly built stores (transient; emptied by boa_cds install)
        ├── inputs/cds-<year>/cache_frontiers/
        │                                  schema v3 frontier stores, keyed on the weather year alone:
        │                                  they hold no availability assumption, so every layer set
        │                                  built on the same weather shares one cache
        ├── costs/<cost_set>/boa_cost_data.xlsx
        │   └── cache_costs/               per-year costs; depends only on the xlsx
        ├── runs/<run>_<hash>/             <hash> = non_scenario_params_hash(search_params);
        │   ├── run.json                   same run label + same params -> same directory
        │   └── outputs/wy<year>/<rho>MWkm2/cov<c>/nc/<REGION>/
        │             optimal_sol_wy<year>_<rho>MWkm2_cov<c>_<REGION>_<inv_year>.nc
        └── lcoe-for-steel-iq/<run>_<hash>/  combined per-run LCOE files the steel sim reads

    ``<rho>``/``<c>`` are dot-free, formatted via ``format_scenario_number`` (e.g.
    ``rho=2.5`` -> ``"02p5MWkm2"``, ``coverage=0.95`` -> ``"cov0p95"``); ``<year>`` is the
    weather year (``wy<year>``), ``<inv_year>`` the investment year.

    Build paths through the helpers rather than inline so a layout change touches one place.
    """

    root: Path
    input_set: str
    cost_set: str
    run: str

    # Single-slot reference data
    input_data_path: Path
    subunits_50m_shapefile_path: Path
    admin1_10m_shapefile_path: Path
    lsm_path: Path
    iso3_grid_path: Path

    # Directories
    data_dir: Path
    inputs_dir: Path
    atlite_output_dir: Path
    zarr_dir: Path
    cav_dir: Path
    cds_dir: Path
    cds_staging_dir: Path
    # Provider data, not derived: the 2.35 GB land-cover raster is the same file for
    # every input set, so it lives beside the other single-slot reference data rather
    # than being re-fetched per set.
    lulc_dir: Path
    costs_dir: Path
    run_dir: Path
    outputs_dir: Path
    cost_cache_dir: Path

    @property
    def run_manifest_path(self) -> Path:
        """Provenance record for the run (input/cost set, versions, CLI args)."""
        return self.run_dir / "run.json"

    def frontier_cache_dir(self, weather_year: int) -> Path:
        """
        ``inputs/cds-<year>/cache_frontiers`` — root for the schema v3 frontier stores.

        Keyed on the weather year and deliberately **not** on ``input_set``. A frontier store
        holds no land-availability assumption — the search box comes from capacity factors and
        the point set from the ERA5 land-sea mask — so every layer set built on the same
        weather shares one cache. Revising the LULC table then rebuilds the ceiling and the
        Grid 2 sidecars but not the physics, and an A/B of two ceilings reads the same physics
        bytes instead of two independently rebuilt copies of them.

        A method rather than a field because the weather year is not known when the config is
        built: ``detect_weather_year`` reads it off the profile stores. That is also what keeps
        it out of ``input_set``, which must not be split (see ``weather_set_name``).

        Known gap: two hand-named input sets holding different profiles for the same year would
        share this cache. Closing it needs a profile signature in the store meta, the
        counterpart of ``availability_signature``. Not built.
        """
        return self.root / "inputs" / weather_set_name(weather_year) / "cache_frontiers"

    def scenario_dir(self, weather_year: int, load_density: float, coverage: float) -> Path:
        """
        ``outputs/wy<weather_year>/<rho>MWkm2/cov<coverage>`` — root for one scenario's artifacts.

        Nested outermost on weather year so one run can hold a full weather-year sweep (each
        year's outputs live in their own subtree) the same way it already holds a coverage/
        load-density sweep, matching how ``frontier_cache_dir`` keys on weather year ahead of
        everything else.

        Keyed on load density (MW/km2, D1), not absolute demand:
        LCOE is exactly baseload-invariant, so an absolute MW figure baked into the path was
        never a real key, only a display value that also happened to vary with latitude once a
        per-pixel demand is derived from it (``load_mw = load_density * pixel_area(lat)``).

        The coverage token is the coverage fraction the run was asked for, formatted like the
        density beside it. It was previously the *uncovered* percentile (``p15`` for 85%
        coverage), which every reader had to invert and which collided under rounding:
        ``--coverage 0.995`` and ``--coverage 1.0`` both produced ``p0`` -- ``format_scenario_number``
        avoids that same failure mode for the current dot-free encoding too.
        """
        return (
            self.outputs_dir
            / f"wy{int(weather_year)}"
            / f"{format_scenario_number(load_density, pad_int_digits=2)}MWkm2"
            / f"cov{format_scenario_number(coverage)}"
        )

    def maps_dir(self, weather_year: int, load_density: float, coverage: float, region: str | None = None) -> Path:
        """Native NetCDF dir for the scenario; per-region when ``region`` given."""
        d = self.scenario_dir(weather_year, load_density, coverage) / "nc"
        return d / region if region else d

    def optimal_sol_filename(
        self, weather_year: int, load_density: float, coverage: float, region: str, year: int
    ) -> str:
        """Self-describing NetCDF filename:
        ``optimal_sol_wy<weather_year>_<rho>MWkm2_cov<c>_<REGION>_<year>.nc``."""
        return (
            f"optimal_sol_wy{int(weather_year)}_"
            f"{format_scenario_number(load_density, pad_int_digits=2)}MWkm2_"
            f"cov{format_scenario_number(coverage)}_{region}_{int(year)}.nc"
        )

    def optimal_sol_path(self, weather_year: int, load_density: float, coverage: float, region: str, year: int) -> Path:
        """Canonical path of one region-year optimal-solution NetCDF."""
        return self.maps_dir(weather_year, load_density, coverage, region) / self.optimal_sol_filename(
            weather_year, load_density, coverage, region, year
        )

    def optimal_sol_year_glob(self, weather_year: int, load_density: float, coverage: float, region: str) -> str:
        """Glob matching every year of one region's optimal-solution NetCDFs."""
        return (
            f"optimal_sol_wy{int(weather_year)}_"
            f"{format_scenario_number(load_density, pad_int_digits=2)}MWkm2_"
            f"cov{format_scenario_number(coverage)}_{region}_*.nc"
        )

    @property
    def lcoe_promotion_dir(self) -> Path:
        """``lcoe-for-steel-iq/<run>`` — combined LCOE files handed to the steel simulation."""
        return self.root / "lcoe-for-steel-iq" / self.run

    def promoted_lcoe_filename(
        self, weather_year: int, load_density: float, coverage: float, year_start: int, year_end: int
    ) -> str:
        """Self-describing combined-LCOE filename:
        ``optimal_lcoe_wy<weather_year>_<rho>MWkm2_cov<c>_<first>_<last>.nc``."""
        return (
            f"optimal_lcoe_wy{int(weather_year)}_"
            f"{format_scenario_number(load_density, pad_int_digits=2)}MWkm2_"
            f"cov{format_scenario_number(coverage)}_{int(year_start)}_{int(year_end)}.nc"
        )

    def promoted_lcoe_path(
        self, weather_year: int, load_density: float, coverage: float, year_start: int, year_end: int
    ) -> Path:
        """Canonical path of one scenario's combined-LCOE file."""
        return self.lcoe_promotion_dir / self.promoted_lcoe_filename(
            weather_year, load_density, coverage, year_start, year_end
        )

    def map_plots_dir(self, weather_year: int, load_density: float, coverage: float, region: str) -> Path:
        """Per-region diagnostic-plot dir for the scenario."""
        return self.scenario_dir(weather_year, load_density, coverage) / "plots" / region

    @property
    def plots_dir(self) -> Path:
        """Non-scenario plot root (e.g. single-point runs)."""
        return self.outputs_dir / "plots"

    @classmethod
    def from_auto_detect(
        cls,
        input_set: str = DEFAULT_SET,
        cost_set: str = DEFAULT_SET,
        run: str | None = None,
    ) -> "PathConfig":
        """Build from the environment-resolved root (see ``default_root``)."""
        return cls.from_root(default_root(), input_set=input_set, cost_set=cost_set, run=run)

    @classmethod
    def from_root(
        cls,
        root: Path,
        input_set: str = DEFAULT_SET,
        cost_set: str = DEFAULT_SET,
        run: str | None = None,
    ) -> "PathConfig":
        """Build the layout under ``root``; ``run`` defaults to ``<input_set>__<cost_set>``."""
        root = Path(root)
        run = run or f"{input_set}__{cost_set}"
        data_dir = root / "data"
        inputs_dir = root / "inputs" / input_set
        costs_dir = root / "costs" / cost_set
        run_dir = root / "runs" / run

        return cls(
            root=root,
            input_set=input_set,
            cost_set=cost_set,
            run=run,
            input_data_path=costs_dir / "boa_cost_data.xlsx",
            # NE 1:50m map_subunits: source of the per-pixel iso3 grid; splits
            # sovereigns into constituent iso3s (France -> FRA + GUF + ...).
            subunits_50m_shapefile_path=data_dir / "ne_50m_admin_0_map_subunits" / "ne_50m_admin_0_map_subunits.shp",
            # NE 1:10m admin-1: province geometry for sub-national cost keys.
            admin1_10m_shapefile_path=data_dir
            / "ne_10m_admin_1_states_provinces"
            / "ne_10m_admin_1_states_provinces.shp",
            lsm_path=data_dir / "lsm_025_deg.nc",
            # Per-pixel ISO3 grid on the 0.25 deg ERA5 grid, used for cost_key derivation.
            iso3_grid_path=data_dir / "iso3_grid.nc",
            data_dir=data_dir,
            inputs_dir=inputs_dir,
            # Legacy atlite NetCDFs, read by the PROFILE_DATA_SOURCE=local_nc backend only.
            atlite_output_dir=inputs_dir / "atlite" / "output",
            cav_dir=inputs_dir / "atlite" / "cav",
            # Live Zarr stores the model reads (profiles + max-capacity), directly in cds-zarr/.
            zarr_dir=inputs_dir / "cds-zarr",
            # Raw CDS downloads (extracted monthly NetCDFs); single-slot — their
            # provenance is fixed by CDS, not by an input set.
            cds_dir=data_dir / "cds",
            # Freshly built stores await promotion to zarr_dir here.
            cds_staging_dir=inputs_dir / "staging",
            lulc_dir=data_dir / "lulc",
            costs_dir=costs_dir,
            run_dir=run_dir,
            outputs_dir=run_dir / "outputs",
            # Design cache follows the profile stores it was built from.
            # Cost cache follows the xlsx it was preprocessed from.
            cost_cache_dir=costs_dir / "cache_costs",
        )
