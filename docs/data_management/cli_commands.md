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

### `steelo-data-prepare` and `steelo-data-recreate`

These two commands are documented in the user guide: see [steelo-data-prepare](../user_guide/cli_commands.md#steelo-data-prepare) and [steelo-data-recreate](../user_guide/cli_commands.md#steelo-data-recreate).

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

# 3. Run a simulation; it prepares its own data from the master Excel
run_simulation
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
```

## Environment Variables

- `STEELO_CACHE_DIR`: Override default cache directory
- `STEELO_OFFLINE_MODE`: Set to "1" to prevent downloads (use cached data only)

## Exit Codes

- `0`: Success
- `1`: General error (validation failed, download error, etc.)
- `2`: Invalid arguments

## Binary Management Commands

`list-binaries`, `download-binaries` and `download-latest` are documented in the user guide: see [List Available Binaries](../user_guide/cli_commands.md#list-available-binaries), [Download Standalone Binaries](../user_guide/cli_commands.md#download-standalone-binaries) and [Download Latest Binaries](../user_guide/cli_commands.md#download-latest-binaries).
