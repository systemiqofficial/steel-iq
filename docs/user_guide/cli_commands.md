# CLI Commands

## Run Steel Model Simulation

The main command to run a steel model simulation with automatic data preparation and caching.

```shell
❯ run_simulation -h
usage: run_simulation [-h] [--start-year START_YEAR] [--end-year END_YEAR]
                      [--plants-json PLANTS_JSON] [--output-dir OUTPUT_DIR]
                      [--output-file OUTPUT_FILE]
                      [--demand-excel DEMAND_EXCEL]
                      [--demand-sheet DEMAND_SHEET]
                      [--demand-scenario DEMAND_SCENARIO]
                      [--scrap-scenario SCRAP_SCENARIO]
                      [--grid-emissions-scenario GRID_EMISSIONS_SCENARIO]
                      [--run-name RUN_NAME] [--location-csv LOCATION_CSV]
                      [--cost-of-x-csv COST_OF_X_CSV]
                      [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
                      [--resume-from-year RESUME_FROM_YEAR]
                      [--master-excel MASTER_EXCEL] [--no-cache]
                      [--force-refresh] [--steelo-home STEELO_HOME]
                      [--cache-stats] [--clear-cache]
                      [--baseload-power-sim-dir BASELOAD_POWER_SIM_DIR]
                      [--enable-clustering] [--plot-tm] [--plot-geo]
                      [--clustering-scope {iso3,plant_group,plant}]
                      [--peg-iron-to-steel-price]
                      [--iron-to-steel-price-ratio IRON_TO_STEEL_PRICE_RATIO]
                      [--random-seed RANDOM_SEED] [--enable-capacity-policy]
                      [--credit-validity-years CREDIT_VALIDITY_YEARS]

Run a full steel model simulation.

options:
  -h, --help            show this help message and exit
  --start-year START_YEAR
                        The year to start the simulation (default: 2025)
  --end-year END_YEAR   The year to end the simulation (default: 2060)
  --plants-json PLANTS_JSON
                        Path to the plants JSON file (default:
                        ./data/fixtures/plants.json)
  --output-dir OUTPUT_DIR
                        Base output directory for simulation results (default:
                        ./outputs)
  --output-file OUTPUT_FILE
                        Path for the output JSON file (default: <output-
                        dir>/pam_simulation_run.json)
  --demand-excel DEMAND_EXCEL
                        Path to the demand excel file (default:
                        ./data/fixtures/2025_05_27 Demand outputs for trade
                        module.xlsx)
  --demand-sheet DEMAND_SHEET
                        Sheet name in the demand excel file (default:
                        'Steel_Demand_Chris Bataille')
  --demand-scenario DEMAND_SCENARIO
                        Scenario name in the 'Demand and scrap availability'
                        sheet used for steel demand (default: BAU)
  --scrap-scenario SCRAP_SCENARIO
                        Scenario name in the same sheet used for scrap
                        availability (default: same as --demand-scenario)
  --grid-emissions-scenario GRID_EMISSIONS_SCENARIO
                        Grid emissivity projection applied at run time, named
                        as in the 'Power grid emissivity' sheet without its
                        'projection_' prefix (default: 'Business As Usual';
                        alternative: 'Net Zero')
  --run-name RUN_NAME   Human-readable run name shown in the interactive plot
                        titles (default: the sim_<timestamp> dir name)
  --location-csv LOCATION_CSV
                        Path to the location CSV file (default:
                        ./data/fixtures/countries.csv)
  --cost-of-x-csv COST_OF_X_CSV
                        Path to the cost of x CSV file (default:
                        ./data/fixtures/cost_of_x.json)
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
  --enable-clustering   Enable furnace group clustering to reduce LP
                        complexity
  --plot-tm             Write the per-year trade maps under plots/TM (off by
                        default; the interactive trade viewers replace them)
  --plot-geo            Write the geospatial PNGs under plots/GEO (off by
                        default)
  --clustering-scope {iso3,plant_group,plant}
                        When clustering is enabled, geographical scope for
                        clustering hot-metal-affected techs. 'iso3' (default):
                        cluster by country. 'plant_group': cluster by
                        corporate group. 'plant': cluster by individual plant.
                        Only affects FGs with hot_metal/dri_*/liquid_iron
                        feedstocks or outputs.
  --peg-iron-to-steel-price
                        Enable iron price pegging to steel price (default:
                        disabled)
  --iron-to-steel-price-ratio IRON_TO_STEEL_PRICE_RATIO
                        Ratio of steel price for iron floor when pegging is
                        enabled (default: 0.8 = 80%)
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
```

`--output-dir` is accepted but currently not used: every run writes to a fresh `$STEELO_HOME/output/sim_<timestamp>/` directory, also linked as `$STEELO_HOME/output_latest`.

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
```

### Features:
- **Automatic Caching**: Caches prepared data based on master Excel content
- **Fast Reruns**: Reuses cached data when running with same inputs
- **Backward Compatibility**: Creates symlinks at `data/` and `output/`
- **Cache Management**: Built-in commands to view and manage cache
- **Iron Price Pegging**: Optionally peg iron prices to steel prices to ensure minimum value ratios (new feature)
- **Capacity Policy**: Optionally enable China's capacity-replacement policy (`--enable-capacity-policy`), gating Chinese replacements and new builds on a national pool of retirement credits

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
                           [--force-refresh]

Prepare all data files for simulation.
```

Key options:
- `--output-dir`: Destination for generated JSON repositories (defaults to `$STEELO_HOME/data`).
- `--cache-dir`: Location of reusable intermediate artefacts.
- `--master-excel-file`: Alternate master input workbook to ingest.
- `--force-refresh`: Rebuild everything even if cached results exist.

### steelo-data-recreate

```shell
❯ steelo-data-recreate -h
usage: steelo-data-recreate [-h] [--package PACKAGE] [--output-dir OUTPUT_DIR]
                            [--cache-dir CACHE_DIR] [--force-download]

Recreate JSON repositories from downloaded data packages (similar to recreate_sample_data).
```

Key options:
- `--package`: Specific package to recreate from (defaults to all required packages).
- `--output-dir`: Output directory for the JSON repositories (default: `./data/repositories`).
- `--cache-dir`: Cache directory for downloaded data.
- `--force-download`: Force re-download of packages instead of using cached archives.

Use `steelo-data-recreate` when you already have the packaged data archives and only need to regenerate the JSON repositories.

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
usage: download-binaries [-h] [--output-dir OUTPUT_DIR] [--platforms {macos,windows} [{macos,windows} ...]] build_id

Download Steel Model standalone binaries from S3

positional arguments:
  build_id              Build identifier (e.g., commit-hash-timestamp format)

options:
  -h, --help            show this help message and exit
  --output-dir OUTPUT_DIR
                        Directory to save downloaded files (default: ./dist/github-action-builds)
  --platforms {macos,windows} [{macos,windows} ...]
                        Platforms to download (default: all platforms)

Examples:
  # Download all platforms for a specific build
  download-binaries a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029
  
  # Download only macOS binaries
  download-binaries a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029 --platforms macos
  
  # Download only Windows binaries
  download-binaries a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060251 --platforms windows
  
  # Download to a custom directory
  download-binaries a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029 --output-dir ./my-downloads
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
