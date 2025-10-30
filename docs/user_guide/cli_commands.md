# CLI Commands

## Run Steel Model Simulation

The main command to run a steel model simulation with automatic data preparation and caching.

```shell
❯ run_simulation -h
usage: run_simulation [-h] [--start-year START_YEAR] [--end-year END_YEAR]
                     [--output-dir OUTPUT_DIR] [--log-level LOG_LEVEL]
                     [--cache-stats] [--clear-cache] [--force-refresh] [--no-cache]
                     [additional options...]

Run a steel model simulation with automatic caching

options:
  -h, --help            show this help message and exit
  --start-year START_YEAR
                        Starting year for simulation (default: 2025)
  --end-year END_YEAR   Ending year for simulation (default: 2050)
  --output-dir OUTPUT_DIR
                        Base output directory (default: $STEELO_HOME/output)
  --log-level LOG_LEVEL
                        Logging level (default: WARNING)

caching options:
  --cache-stats         Show cache statistics and exit
  --clear-cache         Clear preparation cache and exit (Note: use 'steelo-cache clear' for complete cleanup)
  --force-refresh       Force fresh data preparation (bypass cache)
  --no-cache            Disable caching for this run

Examples:
  # Run simulation with default settings
  run_simulation
  
  # Run shorter simulation
  run_simulation --start-year 2025 --end-year 2030
  
  # View cache statistics
  run_simulation --cache-stats
  
  # Clear cache
  run_simulation --clear-cache
  
  # Force fresh preparation
  run_simulation --force-refresh
```

### Features:
- **Automatic Caching**: Caches prepared data based on master Excel content
- **Fast Reruns**: Reuses cached data when running with same inputs
- **Backward Compatibility**: Creates symlinks at `data/` and `output/`
- **Cache Management**: Built-in commands to view and manage cache

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
usage: steelo-data-recreate [-h] [--force-download] [--master-excel MASTER_EXCEL]
                            [--track-timing] [--list-packages]
                            [package_name] [output_dir]

Recreate JSON repositories from downloaded data packages (similar to recreate_sample_data).
```

Use `steelo-data-recreate` when you already have the packaged data archives and only need to regenerate the JSON repositories or inspect package contents.

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
