# Data Management CLI Commands

The steel model provides several CLI commands for managing data packages. All commands start with `steelo-data-`. Additionally, there are commands for managing standalone binaries: `download-binaries` and `list-binaries`.

## Available Commands

### `steelo-data-download`

Download data packages from S3.

```bash
# Download all required packages
steelo-data-download

# Download specific package
steelo-data-download --package core-data

# Force re-download
steelo-data-download --force

# Use custom cache directory
steelo-data-download --cache-dir /path/to/cache
```

**Options:**
- `--cache-dir PATH`: Custom cache directory (default: `~/.steelo/data_cache`)
- `--force`: Force re-download even if already cached
- `--package NAME`: Download specific package instead of all required

### `steelo-data-list`

List all available data packages and their download status.

```bash
steelo-data-list
```

**Output example:**
```
Available Data Packages
┏━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Package      ┃ Version ┃ Size (MB) ┃ Required ┃ Cached ┃ Description                      ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ core-data    │ 1.0.0   │       3.0 │ Yes      │ ✓      │ Core steel model data            │
│ geo-data     │ 1.0.0   │     340.0 │ Yes      │ ✗      │ Geospatial data                  │
│ templates    │ 1.0.0   │       5.0 │ No       │ ✗      │ Excel templates for user input   │
└──────────────┴─────────┴───────────┴──────────┴────────┴──────────────────────────────────┘
```

### `steelo-data-verify`

Verify integrity of cached data packages using checksums.

```bash
steelo-data-verify
```

**Output example:**
```
Data Integrity Check
┏━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Package    ┃ Status   ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━┩
│ core-data  │ ✓ Valid  │
│ geo-data   │ ✗ Invalid│
└────────────┴──────────┘
```

### `steelo-data-validate`

Validate Excel files in a directory for correctness.

```bash
# Validate files in a directory
steelo-data-validate /path/to/excel/files

# Strict mode (warnings become errors)
steelo-data-validate /path/to/excel/files --strict
```

**Arguments:**
- `directory`: Directory containing Excel files to validate

**Options:**
- `--strict`: Treat warnings as errors

### `steelo-data-convert`

Convert Excel files to JSON repositories.

```bash
# Convert Excel files to JSON
steelo-data-convert /path/to/excel /path/to/output

# Strict validation
steelo-data-convert /path/to/excel /path/to/output --strict
```

**Arguments:**
- `input_dir`: Directory containing Excel files
- `output_dir`: Directory for JSON output

**Options:**
- `--strict`: Treat warnings as errors

### `steelo-data-prepare`

Prepare all data files needed for simulation by extracting raw files and creating JSON repositories. This is the primary command for setting up simulation data.

**Note**: The `run_simulation` command now automatically handles data preparation with caching. You typically don't need to run this command separately unless you want to prepare data without running a simulation.

```bash
# Basic usage - prepare all data (uses cache if available)
steelo-data-prepare

# Force fresh preparation (bypass cache and recreate all files)
steelo-data-prepare --force-refresh

# Custom output directory (bypasses caching)
steelo-data-prepare --output-dir ./my-data/fixtures

# Use a specific master Excel file
steelo-data-prepare --master-excel-file /path/to/master_input.xlsx

# Skip files that already exist
steelo-data-prepare --skip-existing

# Force recreation of all files
steelo-data-prepare --no-skip-existing

# List all files that can be prepared
steelo-data-prepare --list-files

# Show detailed progress
steelo-data-prepare --verbose
```

**Options:**
- `--output-dir PATH`: Output directory for data files (default: `data/fixtures`)
- `--cache-dir PATH`: Cache directory for downloaded data
- `--master-excel-file PATH`: Path to custom master Excel file (overrides S3 download)
- `--force-refresh`: Force re-preparation even if cached (bypasses cache and implies --no-skip-existing)
- `--skip-existing`: Skip files that already exist (default: True)
- `--no-skip-existing`: Force recreation of all files
- `--list-files`: List all available files for recreation and exit
- `--verbose`: Show detailed progress during preparation

**Output:**
The command prepares files from three sources in this order:
1. **Master Excel files** - Extracted from the master input Excel
2. **Core data files** - Copied from the core-data package
3. **Derived files** - Generated from other data files
4. **Geo data files** - Extracted from the geo-data package

**Caching Note:**
When used by `run_simulation`, prepared data is automatically cached in `$STEELO_HOME/preparation_cache/` based on the master Excel content hash. Subsequent runs with the same master Excel will reuse the cached data instantly (typically < 1 second). The timing display shows the original preparation time even when using cache, to indicate what work was done originally.

### `steelo-data-recreate`

Recreate all JSON repositories from downloaded data packages (similar to `recreate_sample_data`).

```bash
# Recreate from all required packages
steelo-data-recreate

# Recreate from specific package
steelo-data-recreate --package core-data

# Custom output directory
steelo-data-recreate --output-dir /path/to/output

# Force re-download before recreating
steelo-data-recreate --force-download
```

**Options:**
- `--package NAME`: Process specific package only
- `--output-dir PATH`: Output directory (default: `./data/repositories`)
- `--cache-dir PATH`: Custom cache directory
- `--force-download`: Force re-download packages

### `steelo-cache clear`

Clear all cached data, downloaded packages, and prepared data files for a complete reset.

```bash
# Clear all caches and data (with confirmation)
steelo-cache clear

# Clear all caches but keep 5 most recent preparation caches
steelo-cache clear --keep-recent 5

# Custom cache directories
steelo-cache clear --cache-dir /path/to/prep/cache --data-cache-dir /path/to/data/cache
```

**Options:**
- `--keep-recent N`: Keep N most recent cached preparations (data cache is always fully cleared)
- `--cache-dir PATH`: Preparation cache directory (default: $STEELO_HOME/preparation_cache)
- `--data-cache-dir PATH`: Data cache directory (default: $HOME/.steelo/data_cache)

**What gets cleared:**
- All cached data preparations in `$STEELO_HOME/preparation_cache/`
- All downloaded data packages in `$HOME/.steelo/data_cache/`
- The `data/` directory (including all prepared files)

After running this command, the next `steelo-data-prepare` will perform a complete fresh preparation.

## Cache Management Commands

### `steelo-cache stats`

Show cache statistics including total size and number of cached preparations.

```bash
steelo-cache stats
```

### `steelo-cache list`

List all cached preparations with details.

```bash
steelo-cache list
```

## Common Workflows

### Initial Setup (Recommended)
```bash
# Simply run the simulation - data preparation is automatic with caching
run_simulation
```

The `run_simulation` command now handles everything:
1. Downloads required packages if needed
2. Prepares data (or uses cache if available)
3. Creates backward-compatible symlinks
4. Runs the simulation

### Clear All Caches (Master Excel Update)
When a new master Excel is released, clear all caches and regenerate data:
```bash
# Clear all caches and data files
steelo-cache clear

# Prepare fresh data
steelo-data-prepare

# Run simulation with new master Excel
run_simulation --master-excel-file /path/to/new/master.xlsx
```

### Alternative Setup (Manual Steps)
```bash
# 1. Download required data
steelo-data-download

# 2. Recreate JSON repositories
steelo-data-recreate

# 3. Run simulation using the data
run_simulation --plants-json ./data/repositories/core-data/plants.json
```

### Update Data
```bash
# 1. Force re-download latest data
steelo-data-download --force

# 2. Verify integrity
steelo-data-verify

# 3. Recreate repositories
steelo-data-recreate --force-download
```

### Validate User Data
```bash
# 1. Validate Excel files
steelo-data-validate ./user_data

# 2. If valid, convert to JSON
steelo-data-convert ./user_data ./output

# 3. Use in simulation
run_simulation --plants-json ./output/plants.json
```

## Environment Variables

- `STEELO_CACHE_DIR`: Override default cache directory
- `STEELO_OFFLINE_MODE`: Set to "1" to prevent downloads (use cached data only)

## Exit Codes

- `0`: Success
- `1`: General error (validation failed, download error, etc.)
- `2`: Invalid arguments

## Binary Management Commands

### `download-binaries`

Download standalone Steel Model binaries (Electron builds) from S3.

```bash
# Download all platforms for a specific build
download-binaries a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029

# Download only macOS binaries
download-binaries a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029 --platforms macos

# Download to a custom directory
download-binaries a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029 --output-dir ./my-downloads
```

**Arguments:**
- `build_id`: Build identifier (e.g., commit-hash-timestamp format)

**Options:**
- `--output-dir PATH`: Directory to save downloaded files (default: `./dist/github-action-builds`)
- `--platforms`: Platforms to download (choices: `macos`, `windows`). Default: all platforms

### `list-binaries`

List available Steel Model binaries on S3.

```bash
# List all available builds
list-binaries

# List files in a specific build
list-binaries --build a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029

# List only recent builds
list-binaries --recent 5
```

**Options:**
- `--build`: List files in a specific build
- `--recent N`: Show only the N most recent builds

**Note:** The S3 bucket might not allow listing of all builds. If you know a specific build ID, you can use `--build` to list files in that build.