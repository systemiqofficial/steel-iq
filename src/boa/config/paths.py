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
        ├── runs/<run>/                    one (input_set, cost_set) pairing
        │   ├── run.json                   provenance
        │   └── outputs/<bl>MW/cov<c>/nc/<REGION>/optimal_sol_<bl>MW_cov<c>_<REGION>_<year>.nc
        └── lcoe-for-steel-iq/<run>/       combined per-run LCOE files the steel simulation reads

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

    def scenario_dir(self, baseload_demand: float, coverage: float) -> Path:
        """
        ``outputs/<baseload>MW/cov<coverage>`` — root for one scenario's artifacts.

        The token is the coverage fraction the run was asked for, formatted like the
        baseload beside it. It was previously the *uncovered* percentile (``p15`` for 85%
        coverage), which every reader had to invert and which collided under rounding:
        ``--coverage 0.995`` and ``--coverage 1.0`` both produced ``p0``.
        """
        return self.outputs_dir / f"{baseload_demand:g}MW" / f"cov{coverage:g}"

    def maps_dir(self, baseload_demand: float, coverage: float, region: str | None = None) -> Path:
        """Native NetCDF dir for the scenario; per-region when ``region`` given."""
        d = self.scenario_dir(baseload_demand, coverage) / "nc"
        return d / region if region else d

    def optimal_sol_filename(self, baseload_demand: float, coverage: float, region: str, year: int) -> str:
        """Self-describing NetCDF filename: ``optimal_sol_<bl>MW_cov<c>_<REGION>_<year>.nc``."""
        return f"optimal_sol_{baseload_demand:g}MW_cov{coverage:g}_{region}_{int(year)}.nc"

    def optimal_sol_path(self, baseload_demand: float, coverage: float, region: str, year: int) -> Path:
        """Canonical path of one region-year optimal-solution NetCDF."""
        return self.maps_dir(baseload_demand, coverage, region) / self.optimal_sol_filename(
            baseload_demand, coverage, region, year
        )

    def optimal_sol_year_glob(self, baseload_demand: float, coverage: float, region: str) -> str:
        """Glob matching every year of one region's optimal-solution NetCDFs."""
        return f"optimal_sol_{baseload_demand:g}MW_cov{coverage:g}_{region}_*.nc"

    @property
    def lcoe_promotion_dir(self) -> Path:
        """``lcoe-for-steel-iq/<run>`` — combined LCOE files handed to the steel simulation."""
        return self.root / "lcoe-for-steel-iq" / self.run

    def promoted_lcoe_filename(self, baseload_demand: float, coverage: float, year_start: int, year_end: int) -> str:
        """Self-describing combined-LCOE filename: ``optimal_lcoe_<bl>MW_cov<c>_<first>_<last>.nc``."""
        return f"optimal_lcoe_{baseload_demand:g}MW_cov{coverage:g}_{int(year_start)}_{int(year_end)}.nc"

    def promoted_lcoe_path(self, baseload_demand: float, coverage: float, year_start: int, year_end: int) -> Path:
        """Canonical path of one scenario's combined-LCOE file."""
        return self.lcoe_promotion_dir / self.promoted_lcoe_filename(baseload_demand, coverage, year_start, year_end)

    def map_plots_dir(self, baseload_demand: float, coverage: float, region: str) -> Path:
        """Per-region diagnostic-plot dir for the scenario."""
        return self.scenario_dir(baseload_demand, coverage) / "plots" / region

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
