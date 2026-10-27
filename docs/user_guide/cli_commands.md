# CLI Commands

## Run Steel Model Simulation

The main command to run a steel model simulation with automatic data preparation and caching.

```shell
❯ run_simulation -h
usage: run_simulation [-h] [--start-year START_YEAR] [--end-year END_YEAR]
                      [--output-dir OUTPUT_DIR]
                      [--demand-scenario DEMAND_SCENARIO]
                      [--scrap-scenario SCRAP_SCENARIO]
                      [--grid-emissions-scenario GRID_EMISSIONS_SCENARIO]
                      [--run-name RUN_NAME]
                      [--hydrogen-ceiling-percentile HYDROGEN_CEILING_PERCENTILE]
                      [--intraregional-trade | --no-intraregional-trade]
                      [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
                      [--resume-from-year RESUME_FROM_YEAR]
                      [--master-excel MASTER_EXCEL] [--no-cache]
                      [--force-refresh] [--steelo-home STEELO_HOME]
                      [--cache-stats] [--clear-cache]
                      [--baseload-power-sim-dir BASELOAD_POWER_SIM_DIR]
                      [--clustering | --no-clustering] [--plot-tm]
                      [--plot-geo]
                      [--clustering-scope {iso3,plant_group,plant}]
                      [--peg-iron-to-steel-price]
                      [--iron-to-steel-price-ratio IRON_TO_STEEL_PRICE_RATIO]
                      [--random-seed RANDOM_SEED] [--enable-capacity-policy]
                      [--credit-validity-years CREDIT_VALIDITY_YEARS]
                      [--cbam-rebate] [--optimization-solver {highs,gurobi}]

Run a full steel model simulation.

options:
  -h, --help            show this help message and exit
  --start-year START_YEAR
                        The year to start the simulation (default: 2025)
  --end-year END_YEAR   The year to end the simulation (default: 2060)
  --output-dir OUTPUT_DIR
                        Base output directory for simulation results (default:
                        $STEELO_HOME/output)
  --demand-scenario DEMAND_SCENARIO
                        Steel demand scenario of the run, named as in the
                        'Demand and scrap availability' sheet and picked at
                        run time from the prepared data; a name it lacks stops
                        the run before it starts (default: BAU)
  --scrap-scenario SCRAP_SCENARIO
                        Scrap availability scenario of the run, named and
                        picked the same way (default: same as --demand-
                        scenario)
  --grid-emissions-scenario GRID_EMISSIONS_SCENARIO
                        Grid emissivity projection applied at run time, named
                        as in the 'Power grid emissivity' sheet without its
                        'projection_' prefix (default: 'Business As Usual';
                        alternative: 'Net Zero')
  --run-name RUN_NAME   Human-readable run name shown in the interactive plot
                        titles (default: the sim_<timestamp> dir name); a name
                        already in use under the output base gets _2, _3, and
                        so on
  --hydrogen-ceiling-percentile HYDROGEN_CEILING_PERCENTILE
                        Percentile of a region's LCOH used as the regional
                        hydrogen price cap (default: the GeoConfig value, 100,
                        which disables the cap)
  --intraregional-trade, --no-intraregional-trade
                        Allow hydrogen imports between linked regions
                        (default: the GeoConfig value, off)
  --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Set the logging level (default: WARNING)
  --resume-from-year RESUME_FROM_YEAR
                        Resume simulation from a checkpoint at the specified
                        year
  --master-excel MASTER_EXCEL
                        Path to master Excel file (uses default or downloads
                        if not provided)
  --no-cache            Disable preparation cache
  --force-refresh       Force data re-preparation even if cached
  --steelo-home STEELO_HOME
                        STEELO_HOME directory (default: ~/.steelo or
                        $STEELO_HOME)
  --cache-stats         Show cache statistics and exit
  --clear-cache         Clear preparation cache and exit
  --baseload-power-sim-dir BASELOAD_POWER_SIM_DIR
                        Path to BOA-generated baseload power simulation output
                        directory (overrides default)
  --clustering, --no-clustering
                        Cluster furnace groups to reduce LP complexity
                        (default: on)
  --plot-tm             Write the per-year trade maps under plots/TM (off by
                        default; the interactive trade viewers replace them)
  --plot-geo            Write the geospatial PNGs under plots/GEO (off by
                        default)
  --clustering-scope {iso3,plant_group,plant}
                        When clustering is enabled, geographical scope for
                        clustering hot-metal-affected techs. 'plant'
                        (default): cluster by individual plant. 'plant_group':
                        cluster by corporate group. 'iso3': cluster by
                        country. Only affects FGs with
                        hot_metal/dri_*/liquid_iron feedstocks or outputs.
  --peg-iron-to-steel-price
                        Enable iron price pegging to steel price (default:
                        disabled)
  --iron-to-steel-price-ratio IRON_TO_STEEL_PRICE_RATIO
                        Ratio of steel price for iron floor when pegging is
                        enabled (default: 0.8)
  --random-seed RANDOM_SEED
                        Seed for the run-time RNGs shared by the plant agent,
                        geospatial and trade LP modules (default: 42); data
                        preparation keeps its own fixed seed
  --enable-capacity-policy
                        Enable China's capacity-replacement policy (default:
                        disabled)
  --credit-validity-years CREDIT_VALIDITY_YEARS
                        Years a capacity-pool credit may sit banked before it
                        expires; requires --enable-capacity-policy (default:
                        no expiry)
  --cbam-rebate         Rebate the carbon already paid on exports leaving a
                        carbon border region (default: disabled)
  --optimization-solver {highs,gurobi}
                        Trade LP solver backend (default: highs). 'gurobi'
                        requires your own Gurobi license and a gurobipy
                        installation reachable from wherever the simulation
                        runs; you must follow the terms of your license.
```

Gurobi is commercial software that this project does not license. Selecting `--optimization-solver gurobi` prints a license reminder. You need your own Gurobi license and must use it as its license agreement allows (for example, an academic license is not valid for commercial use). Install `gurobipy` with `uv sync --extra gurobi`.

Every run writes to a fresh `sim_<timestamp>/` directory under `--output-dir` (default `$STEELO_HOME/output`), also linked as `latest` in that output directory and as `$STEELO_HOME/output_latest`.

Examples:

```shell
# Run simulation with default settings
run_simulation

# Run shorter simulation
run_simulation --start-year 2025 --end-year 2030

# Run against a specific master input workbook
run_simulation --master-excel ./master_input/my_master.xlsx

# Enable iron price pegging with default 80% ratio
run_simulation --peg-iron-to-steel-price

# Enable iron price pegging with custom 75% ratio
run_simulation --peg-iron-to-steel-price --iron-to-steel-price-ratio 0.75

# View cache statistics
run_simulation --cache-stats

# Clear cache
run_simulation --clear-cache

# Force fresh preparation
run_simulation --force-refresh

# Run with China's capacity-replacement policy enabled, credits expiring after 5 years
run_simulation --enable-capacity-policy --credit-validity-years 5

# Cap regional hydrogen prices at the 20th LCOH percentile and allow hydrogen imports between linked regions
run_simulation --hydrogen-ceiling-percentile 20 --intraregional-trade

# Solve the trade LP with Gurobi instead of HiGHS (requires a license)
run_simulation --optimization-solver gurobi
```

A run ends by printing where its results are and how to open them in the [Scenario Viewer](scenario_viewer.md):

```text
Simulation completed!
Results in: /Users/me/.steelo/output/sim_20261023_101500
View it: steelo-viewer /Users/me/.steelo/output/sim_20261023_101500
Latest symlink: /Users/me/.steelo/output/latest
```

### Features:
- **Automatic Caching**: Caches prepared data keyed on the master Excel's content and the cache version
- **Fast Reruns**: Reuses cached data when running with the same master Excel; one preparation holds every demand and scrap scenario, so changing the scenario reuses it too
- **Backward Compatibility**: Creates symlinks at `data/` and `output/`
- **Cache Management**: Built-in commands to view and manage cache
- **Iron Price Pegging**: Optionally peg iron prices to steel prices to ensure minimum value ratios (new feature)
- **Capacity Policy**: Optionally enable China's capacity-replacement policy (`--enable-capacity-policy`), gating Chinese replacements and new builds on a national pool of retirement credits

## steelo-viewer

Serves the [Scenario Viewer](scenario_viewer.md) for local runs on `127.0.0.1`, or rebuilds a run's viewer files.

```shell
❯ steelo-viewer -h
usage: steelo-viewer [-h] [--steelo-home STEELO_HOME]
                     [--output-dir OUTPUT_DIR] [--port PORT] [--no-browser]
                     [RUN_DIR ...]

Serve the scenario viewer for local runs.

positional arguments:
  RUN_DIR               run folder to serve (default: every run)

options:
  -h, --help            show this help message and exit
  --steelo-home STEELO_HOME
                        STEELO_HOME directory (default: ~/.steelo or
                        $STEELO_HOME)
  --output-dir OUTPUT_DIR
                        output base holding the run folders (default:
                        $STEELO_HOME/output)
  --port PORT           port to serve on (default: a free port)
  --no-browser          do not open the viewer in the browser

steelo-viewer regenerate RUN_DIR [RUN_DIR ...] rebuilds the viewer files of
finished runs; see steelo-viewer regenerate --help.
```

- With no `RUN_DIR`, every run folder directly under `--output-dir` is served; symlinked folders such as `latest` are skipped there. Named `RUN_DIR`s are served alone, by their real paths, and `--output-dir` and `--steelo-home` are not used.
- A run is listed once its folder holds `viewer/run.json`. The viewer looks for runs again each time the page loads.
- The command prints the viewer's address and "Runs served: N; press Ctrl-C to stop", opens the default browser unless `--no-browser` is given, and serves until Ctrl-C. It answers only requests addressed to `127.0.0.1` or `localhost`.
- It exits with code 1 when the output folder does not exist, when no run holds `viewer/run.json`, or when two named run folders share a folder name.

```bash
# Every run under $STEELO_HOME/output
steelo-viewer

# The latest run only, on port 8080
steelo-viewer ~/.steelo/output/latest --port 8080
```

### steelo-viewer regenerate

```shell
❯ steelo-viewer regenerate -h
usage: steelo-viewer regenerate [-h] [--master-excel MASTER_EXCEL]
                                RUN_DIR [RUN_DIR ...]

Rebuild the viewer files of finished runs.

positional arguments:
  RUN_DIR               run folder to rebuild

options:
  -h, --help            show this help message and exit
  --master-excel MASTER_EXCEL
                        master workbook to read instead of the one each run
                        recorded
```

Rebuilds the `viewer/` folder of finished runs. Each run folder must hold a `post_processed_*.csv`, `simulation_config.json`, `preparation_metadata.json` and `plots/interactive/emissions.html`, and the master workbook the run recorded, or the one `--master-excel` names, must exist. Every run is checked first: when an input is missing, the command names it, writes nothing for any run and exits with code 1. Otherwise each run's `viewer/` folder is deleted and written again, and the plot pages and model outputs are left alone.

```bash
steelo-viewer regenerate ~/.steelo/output/sim_20261023_101500
```

## Data Preparation Commands

These commands prepare the datasets required for simulations. They are safe to run repeatedly; the tooling handles caching and incremental refreshes.

### steelo-data-prepare

```shell
❯ steelo-data-prepare -h
usage: steelo-data-prepare [-h] [--output-dir OUTPUT_DIR]
                           [--cache-dir CACHE_DIR]
                           [--master-excel-file MASTER_EXCEL_FILE]
                           [--geo-version GEO_VERSION] [--skip-existing]
                           [--no-skip-existing] [--list-files] [--verbose]
                           [--force-refresh] [--use-furnace-units-sheet]
                           [--use-plants-sheet]

Prepare all data files for simulation.
```

Prepares every data file a simulation needs by extracting the raw files and creating the JSON repositories. `run_simulation` already prepares data automatically and caches the result, so you only need this command to prepare data without running a simulation.

```bash
# Prepare all data (uses the cache if available)
steelo-data-prepare

# Force fresh preparation (bypasses the cache and recreates all files)
steelo-data-prepare --force-refresh

# Use a specific master input workbook
steelo-data-prepare --master-excel-file /path/to/master_input.xlsx

# List all files that can be prepared
steelo-data-prepare --list-files
```

Options:
- `--output-dir`: Destination for the prepared files (default: `./data`). A custom directory bypasses the cache.
- `--cache-dir`: Cache directory for downloaded data.
- `--master-excel-file`: Master input workbook to use instead of the downloaded one.
- `--geo-version`: Version of the geo-data package to use, for example `1.1.0-dev`. Can also be set with the `STEELO_GEO_VERSION` environment variable.
- `--force-refresh`: Prepare again even if cached results exist. Implies `--no-skip-existing`.
- `--skip-existing` / `--no-skip-existing`: Keep files that already exist (default) or recreate all of them.
- `--list-files`: List every file that can be prepared, then exit.
- `--verbose`: Show detailed progress.
- `--use-furnace-units-sheet`: Build the starting fleet from the `Furnace units` sheet (default).
- `--use-plants-sheet`: Build it from the older `Iron and steel plants` sheet instead, for workbooks authored before the `Furnace units` sheet existed. See the [Master Input Reference](master_input_reference.md#furnace-units).

The command prepares files from four sources, in this order:
1. **Master Excel files**: extracted from the master input workbook.
2. **Core data files**: copied from the core-data package.
3. **Derived files**: generated from other data files.
4. **Geo data files**: extracted from the geo-data package.

**Caching**: when `run_simulation` prepares data, it caches the result in `$STEELO_HOME/preparation_cache/`, keyed on the content of the master workbook and the cache version. One preparation holds every demand and scrap scenario, so no scenario is part of the key. Later runs with the same workbook reuse the cache, whichever scenarios they pick, typically in under a second. The timing shown is the original preparation time, even when the cache is used.

### steelo-data-recreate

```shell
❯ steelo-data-recreate -h
usage: steelo-data-recreate [-h] [--package PACKAGE] [--output-dir OUTPUT_DIR]
                            [--cache-dir CACHE_DIR] [--force-download]
                            [--use-furnace-units-sheet] [--use-plants-sheet]

Recreate JSON repositories from downloaded data packages (similar to recreate_sample_data).
```

Key options:
- `--package`: Specific package to recreate from (defaults to all required packages).
- `--output-dir`: Output directory for the JSON repositories (default: `./data/repositories`).
- `--cache-dir`: Cache directory for downloaded data.
- `--force-download`: Force re-download of packages instead of using cached archives.
- `--use-furnace-units-sheet` / `--use-plants-sheet`: Which sheet builds the starting fleet, as for `steelo-data-prepare` (default: `Furnace units`).

Use `steelo-data-recreate` when you already have the packaged data archives and only need to regenerate the JSON repositories.

```bash
# Recreate from one package only
steelo-data-recreate --package core-data

# Download the packages again before recreating
steelo-data-recreate --force-download
```

### generate-geo-options

```shell
❯ generate-geo-options -h
usage: generate-geo-options [-h] [--fixtures-dir FIXTURES_DIR] [--out OUT]

Generate geo_options.csv (authorable geo-keys) from a prepared
geo_hierarchy.json

options:
  -h, --help            show this help message and exit
  --fixtures-dir FIXTURES_DIR
                        Directory containing geo_hierarchy.json (default:
                        ~/.steelo/data/fixtures)
  --out OUT             Output CSV path (default: ./geo_options.csv)
```


Writes the list of province keys (`CHN:CN-HE` and so on) that the master workbook can use in its geo-keyed sheets, one row per unit. Data preparation already writes `geo_options.csv` to the data root when the geo-data package includes the Natural Earth admin-1 shapefile; run this command to regenerate it after preparation, or to write it elsewhere. It fails if `geo_hierarchy.json` has not been prepared yet. See [Sub-national Geography](../domain_simulation_logic/sub_national_geography.md).

## List Available Binaries

This command line entrypoint lists available Steel Model standalone binaries on S3, showing build information, platforms, and download URLs.

```shell
❯ list-binaries -h
usage: list-binaries [-h] [--build BUILD] [--recent RECENT]

List available Steel Model binaries on S3

options:
  -h, --help       show this help message and exit
  --build BUILD    List files in a specific build
  --recent RECENT  Show only the N most recent builds

Examples:
  # List all available builds
  list-binaries
  
  # List files in a specific build
  list-binaries --build a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029
  
  # List only recent builds
  list-binaries --recent 5
```

Example output:
```shell
❯ list-binaries --recent 3
Available builds:

Commit     Date/Time            Platforms                 Download URLs
------------------------------------------------------------------------------------------------------------------------
4e979b2    2025-07-04 07:46:50  Windows                   Win: https://github-action-artifacts-steel-model.s3.eu-north-1.amazonaws.com/builds/20250704-074650-4e979b2/steelo-electron-windows-20250704-074650-4e979b2.zip
4e979b2    2025-07-04 07:43:52  macOS                     Mac: https://github-action-artifacts-steel-model.s3.eu-north-1.amazonaws.com/builds/20250704-074352-4e979b2/steelo-electron-macos-20250704-074352-4e979b2.tar.gz
60cd434    2025-07-03 20:18:47  Windows                   Win: https://github-action-artifacts-steel-model.s3.eu-north-1.amazonaws.com/builds/20250703-201847-60cd434/steelo-electron-windows-20250703-201847-60cd434.zip

Total: 3 build(s)
```

## Download Standalone Binaries

This command line entrypoint downloads the Steel Model standalone binaries (Electron apps) from S3.

```shell
❯ download-binaries -h
usage: download-binaries [-h] [--output-dir OUTPUT_DIR] [--platforms {macos,windows} [{macos,windows} ...]] identifier

Download Steel Model standalone binaries from S3

positional arguments:
  identifier            Build identifier or commit ID (e.g., '4e979b2' or 'a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029')

options:
  -h, --help            show this help message and exit
  --output-dir OUTPUT_DIR
                        Directory to save downloaded files (default: ./dist/github-action-builds)
  --platforms {macos,windows} [{macos,windows} ...]
                        Platforms to download (default: all platforms)

Examples:
  # Download using commit ID (as shown in list-binaries)
  download-binaries 4e979b2

  # Download using full build ID
  download-binaries a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029
  
  # Download only macOS binaries using commit ID
  download-binaries 4e979b2 --platforms macos
  
  # Download only Windows binaries using commit ID
  download-binaries 4e979b2 --platforms windows
  
  # Download to a custom directory
  download-binaries 4e979b2 --output-dir ./my-downloads
```

## Download Latest Binaries

This command line entrypoint downloads the latest Steel Model standalone binaries from S3 for a specific platform.

```shell
❯ download-latest -h
usage: download-latest [-h] --platform {windows,macos} [--output-dir OUTPUT_DIR]

Download latest Steel Model binaries from S3

options:
  -h, --help            show this help message and exit
  --platform {windows,macos}
                        Platform to download (windows or macos)
  --output-dir OUTPUT_DIR
                        Directory to save downloaded files (default: ./dist/github-action-builds)

Examples:
  # Download latest Windows binary
  download-latest --platform windows
  
  # Download latest macOS binary
  download-latest --platform macos
  
  # Download to custom directory
  download-latest --platform windows --output-dir ./my-downloads
```
