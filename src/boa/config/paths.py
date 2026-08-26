import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SET = "default"


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
        ├── data/                          single slot: shapefiles, lsm, iso3 grid, cds/ raw NetCDFs
        ├── inputs/<input_set>/            profile + max-capacity stores (cds-zarr/, lulc/, atlite/)
        │   ├── staging/                   freshly built stores (transient; emptied by boa_cds install)
        │   └── cache_designs/             year-independent designs; depends only on the stores
        ├── costs/<cost_set>/boa_cost_data.xlsx
        │   └── cache_costs/               per-year costs; depends only on the xlsx
        ├── runs/<run>/                    one (input_set, cost_set) pairing
        │   ├── run.json                   provenance
        │   └── outputs/<bl>MW/p<p>/nc/<REGION>/optimal_sol_<bl>MW_p<p>_<REGION>_<year>.nc
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
    lulc_dir: Path
    costs_dir: Path
    run_dir: Path
    outputs_dir: Path
    design_cache_dir: Path
    cost_cache_dir: Path

    @property
    def run_manifest_path(self) -> Path:
        """Provenance record for the run (input/cost set, versions, CLI args)."""
        return self.run_dir / "run.json"

    def scenario_dir(self, baseload_demand: float, p: int) -> Path:
        """``outputs/<baseload>MW/p<p>`` — root for one scenario's artifacts."""
        return self.outputs_dir / f"{baseload_demand:g}MW" / f"p{int(p)}"

    def maps_dir(self, baseload_demand: float, p: int, region: str | None = None) -> Path:
        """Native NetCDF dir for the scenario; per-region when ``region`` given."""
        d = self.scenario_dir(baseload_demand, p) / "nc"
        return d / region if region else d

    def optimal_sol_filename(self, baseload_demand: float, p: int, region: str, year: int) -> str:
        """Self-describing NetCDF filename: ``optimal_sol_<bl>MW_p<p>_<REGION>_<year>.nc``."""
        return f"optimal_sol_{baseload_demand:g}MW_p{int(p)}_{region}_{int(year)}.nc"

    def optimal_sol_path(self, baseload_demand: float, p: int, region: str, year: int) -> Path:
        """Canonical path of one region-year optimal-solution NetCDF."""
        return self.maps_dir(baseload_demand, p, region) / self.optimal_sol_filename(baseload_demand, p, region, year)

    def optimal_sol_year_glob(self, baseload_demand: float, p: int, region: str) -> str:
        """Glob matching every year of one region's optimal-solution NetCDFs."""
        return f"optimal_sol_{baseload_demand:g}MW_p{int(p)}_{region}_*.nc"

    @property
    def lcoe_promotion_dir(self) -> Path:
        """``lcoe-for-steel-iq/<run>`` — combined LCOE files handed to the steel simulation."""
        return self.root / "lcoe-for-steel-iq" / self.run

    def promoted_lcoe_filename(self, baseload_demand: float, p: int, year_start: int, year_end: int) -> str:
        """Self-describing combined-LCOE filename: ``optimal_lcoe_<bl>MW_p<p>_<first>_<last>.nc``."""
        return f"optimal_lcoe_{baseload_demand:g}MW_p{int(p)}_{int(year_start)}_{int(year_end)}.nc"

    def promoted_lcoe_path(self, baseload_demand: float, p: int, year_start: int, year_end: int) -> Path:
        """Canonical path of one scenario's combined-LCOE file."""
        return self.lcoe_promotion_dir / self.promoted_lcoe_filename(baseload_demand, p, year_start, year_end)

    def map_plots_dir(self, baseload_demand: float, p: int, region: str) -> Path:
        """Per-region diagnostic-plot dir for the scenario."""
        return self.scenario_dir(baseload_demand, p) / "plots" / region

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
            lulc_dir=inputs_dir / "lulc",
            costs_dir=costs_dir,
            run_dir=run_dir,
            outputs_dir=run_dir / "outputs",
            # Design cache follows the profile stores it was built from.
            design_cache_dir=inputs_dir / "cache_designs",
            # Cost cache follows the xlsx it was preprocessed from.
            cost_cache_dir=costs_dir / "cache_costs",
        )
