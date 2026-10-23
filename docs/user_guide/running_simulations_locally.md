# Running Steel Model Simulations Locally

This guide explains how to run the steel model simulation during local development and how to integrate new data sources into the simulation pipeline.

:::{only} public
## Quick Start (CLI)

After installing the Steel Model package, run a simulation from the command line:

```bash
run_simulation --start-year 2025 --end-year 2030
```

Common options:
- `--start-year` / `--end-year`: define the scenario horizon.
- `--master-excel`: the master input workbook to prepare and run against (default: the packaged one, downloaded if needed).
- `--log-level`: control verbosity (`INFO`, `DEBUG`, etc.).
- `--demand-scenario` / `--scrap-scenario`: pick the `Scenario` rows of the master Excel "Demand and scrap availability" sheet used for steel demand and for scrap availability (both default to `BAU`; `--scrap-scenario` falls back to the demand scenario). Both are picked at run time from the workbook's one preparation, which holds every scenario of the sheet; a name it lacks stops the run before it starts, with the names it holds.
- `--run-name`: human-readable run name shown in the interactive viewer titles (default: the `sim_<timestamp>` output directory name); a name that a run under the output directory already holds gets `_2`, `_3` and so on. The viewers themselves are described in [Outputs and postprocessing](../domain_simulation_logic/overarching_elements/outputs_and_postprocessing.md).
- `--enable-capacity-policy`: switch on China's capacity-replacement policy, which gates Chinese replacements and new builds on a national pool of retirement credits (off by default; needs the three `Capacity pool - …` sheets in the master Excel). `--credit-validity-years N` caps how long a banked credit stays spendable (default: no expiry). Policy runs additionally write `data/policy/` CSVs and `plots/capacity_pool/` charts, described in [Outputs and postprocessing](../domain_simulation_logic/overarching_elements/outputs_and_postprocessing.md).
- `--cbam-rebate`: rebate the carbon already paid on exports leaving a carbon border region, mirroring the import charge (off by default; see [Export rebates](../domain_simulation_logic/trade_model/trade_policy.md#export-rebates)).

Each run writes its plots, CSVs and logs to a fresh `sim_<timestamp>/` directory under `--output-dir` (default `$STEELO_HOME/output`), also linked as `$STEELO_HOME/output_latest`, and ends by writing the `viewer/` files that the [Scenario Viewer](scenario_viewer.md) reads: open the run with `steelo-viewer <run folder>`. Review the [Configuration](configuration.md) guide for a comprehensive list of parameters and environment variables.

## Custom Data Overview

To experiment with bespoke datasets:

1. Prepare files that conform to the schemas referenced in the configuration guide.
2. Point the CLI at your customised master Excel, for example:

   ```bash
   run_simulation --master-excel ./my_data/master_input.xlsx
   ```

3. Inspect the generated outputs (plots, CSVs, logs) under the run's `sim_<timestamp>` directory, or open the run in the [Scenario Viewer](scenario_viewer.md) with `steelo-viewer`.
:::

:::{only} not public
## Prerequisites

Before running simulations, ensure you have:
- Python 3.13 installed (via `uv python install 3.13`)
- Virtual environment activated (`source .venv/bin/activate`)
- All dependencies installed (`uv sync`)

## Data Pipeline Architecture

The steel model follows a structured data flow from raw inputs to simulation execution:

### 1. Data Storage & Caching
- **S3 Storage**: Raw data packages (core-data, geo-data) are stored in S3 buckets
- **Local Cache**: Downloaded data is cached in `$STEELO_HOME/data_cache/` to avoid repeated downloads
- **Preparation Cache**: Processed data is cached in `$STEELO_HOME/preparation_cache/` based on master Excel content hash
- **Django Models**: In web mode, `DataPackage` models store the zip archives in Django's media directory (not in `$STEELO_HOME`)

### 2. Data Transformation
The system transforms raw input data through two parallel paths:

**CLI Path:**
- Raw data → Preprocessing → Files in `$STEELO_HOME/preparation_cache/prep_<hash>/data/`
- Creates JSON repositories and processed CSV/Excel files
- Symlinks created at `project_root/data/` for backward compatibility

**Django Path:**
- Raw data → `DataPackage` models → `DataPreparation` models
- Stores processed data in Django's media directory

### 3. Configuration & Execution
- A `SimulationConfig` object is created with pointers to all required data files
- The config is passed to `SimulationRunner`, which distributes it to all modules
- No downstream module needs to know about the original data sources

### Integrating New Data Sources (e.g., Master Excel)

When adding new data sources like master Excel files, follow this pattern:

1. **Create an Adapter**: Write a transformation module in `src/steelo/adapters/` that:
   - Takes the path to your Excel file as input
   - Returns domain model instances as output
   - Example: `adapters/dataprocessing/master_excel_reader.py`

2. **Extend SimulationConfig**: Add fields for your new data to the `SimulationConfig` class

3. **Wire Through the System**:
   - Pass data via `SimulationConfig` → repositories or `bus.env`
   - Access in your module via event/command handlers

4. **Feature Flag**: Add a flag in `global_variables.py` (default `False`) to enable/disable your feature:
   ```python
   USE_MASTER_EXCEL = False  # Enable when ready
   ```

This approach ensures your changes don't break existing functionality and can be easily replaced when the system officially adopts the master input file.

## Method 1: Programmatic Execution (Python/Notebook)

The programmatic approach gives you full control over the simulation configuration and is ideal for:
- Jupyter notebook analysis
- Custom simulation scenarios
- Integration with other Python tools
- Batch processing

### Quick Example

```python
from pathlib import Path
from steelo.bootstrap import bootstrap_simulation
from steelo.simulation import SimulationConfig
from steelo.domain import Year

config = SimulationConfig.from_data_directory(
    start_year=Year(2025),
    end_year=Year(2030),
    data_dir=Path("./data"),
    output_dir=Path("./test_outputs"),
)

runner = bootstrap_simulation(config)
results = runner.run()

# results is {"price": trace_price, "cost": cost_breakdown,
#             "capacity": trace_capacity, "production": trace_production}
# trace_price has shape {year: {product: price_usd_per_t}}
print(f"2030 steel price: {results['price'][2030]['steel']}")
print(f"2030 production: {results['production'][2030]}")
```

### Custom Paths Example

`SimulationConfig.from_data_directory()` accepts override kwargs that are forwarded to `SimulationConfig.__init__`. Use it to point at a non-default data directory and tweak any config field — including `plots_dir`, `start_year`/`end_year`, and so on:

```python
config = SimulationConfig.from_data_directory(
    start_year=Year(2025),
    end_year=Year(2050),
    data_dir=Path("./my_data"),                      # contains master_input.xlsx + fixtures/
    output_dir=Path("./custom_outputs"),
    plots_dir=Path("./custom_outputs/plots"),        # defaults to output_dir/plots
)
```

To swap in a single input file (e.g. a plants JSON), point `data_dir` at a directory that contains the customised files using the standard layout.

### Technology Constraints Example

```python
from steelo.simulation_types import get_default_technology_settings, TechnologySettings

# Create technology settings with specific constraints
tech_settings = get_default_technology_settings()

# The model looks a technology up by its normalised code: upper case, letters and digits only (BF, DRI, MOE)

# Ban blast furnaces by setting allowed=False
tech_settings['BF'] = TechnologySettings(
    allowed=False,
    from_year=2025,
    to_year=None
)

# Allow DRI only from 2030
tech_settings['DRI'] = TechnologySettings(
    allowed=True,
    from_year=2030,
    to_year=None
)

# Disable molten oxide electrolysis
tech_settings['MOE'] = TechnologySettings(
    allowed=False,
    from_year=2025,
    to_year=None
)

config = SimulationConfig.from_data_directory(
    start_year=Year(2025),
    end_year=Year(2040),
    data_dir=Path("./data"),                         # master_excel_path defaults to data_dir/master_input.xlsx
    output_dir=Path("./test_outputs"),
    technology_settings=tech_settings,
)
```

For more examples, see `examples/run_simulation_example.py`.

## Caching System

The CLI implements a content-based caching system that significantly speeds up repeated simulations:

### How It Works

1. **Content Hashing**: The master Excel file's content is hashed using SHA256, together with the cache version, to create the cache key. No scenario is part of it: one preparation holds every demand and scrap scenario of the workbook, and each run picks its own
2. **Cache Storage**: Prepared data is stored in `$STEELO_HOME/preparation_cache/prep_<hash>/`
3. **Fast Lookups**: An index file tracks all cached preparations for instant lookups
4. **Automatic Reuse**: When running with the same master Excel, cached data is reused instantly

### Cache Management Commands

```bash
# View cache statistics
steelo-cache stats

# List all cached preparations
steelo-cache list

# Clear all cached data
steelo-cache clear

# Clear old caches but keep the 3 most recent preparations
steelo-cache clear --keep-recent 3

# Force fresh preparation (bypass cache)
run_simulation --force-refresh

# Disable caching entirely
run_simulation --no-cache
```

### Cache Versioning

The cache system includes automatic version tracking. The cache key holds the cache version (`CACHE_VERSION` in `cache_manager.py`) beside the workbook's content, so a new cache version leaves every older preparation unused. A preparation made before scenarios were picked at run time holds no `fixtures/scenarios.json`; its cache version is older, so the CLI prepares the workbook again, while the web interface's run form refuses it and asks for the master to be prepared again. When the code that processes data changes, old caches are automatically invalidated. This ensures you always get correctly processed data without manual intervention.

If you encounter issues with outdated cached data:
1. The cache version is automatically bumped when processing code changes
2. Old caches are invalidated when detected
3. Use `--force-refresh` to bypass all caching if needed

### Directory Structure

```
$STEELO_HOME/
├── preparation_cache/
│   ├── index.json                    # Fast lookup index
│   ├── prep_a1b2c3d4/               # Cached preparation
│   │   ├── data/                    # Prepared data files
│   │   │   └── fixtures/            # JSON repositories
│   │   │       ├── scenarios.json   # Each demand and scrap scenario's file
│   │   │       ├── demand_scenarios/  # demand_centers.json per demand scenario
│   │   │       └── scrap_scenarios/   # suppliers.json per scrap scenario
│   │   └── metadata.json            # Cache metadata
│   └── prep_e5f6g7h8/               # Another cached preparation
├── output/                          # Simulation outputs
│   ├── sim_20240726_143052/        # Timestamped simulation
│   │   └── viewer/                 # Scenario viewer files
│   └── latest -> sim_20240726...   # Symlink to latest
├── data -> preparation_cache/...    # Symlink to latest preparation
└── output_latest -> output/sim_...  # Symlink to latest output
```

### Backward Compatibility

For backward compatibility with existing scripts, symlinks are automatically created:
- `project_root/data/` → Latest cached preparation
- `project_root/output/` → Latest simulation output

If these directories already exist, they are backed up to `data_backup_<timestamp>` and `output_backup_<timestamp>`.

## Method 2: Command-Line Interface (CLI)

The CLI approach is useful for automated runs, testing, and debugging.

### Quick Start

For most cases, you only need one command:

```bash
# Run the simulation (automatically prepares data if needed)
run_simulation
```

The `run_simulation` command will automatically:
- Download required data packages from S3 if not cached
- Prepare all necessary data files
- Use cached preparations when possible for faster startup
- Run the actual simulation

### Getting Fresh Data

If you need to force fresh data preparation (e.g., after fixing bugs or updating master Excel):

```bash
# Method 1: Force refresh during simulation
run_simulation --force-refresh

# Method 2: Clear cache and run
steelo-cache clear
run_simulation

# Method 3: Prepare data explicitly with force refresh
steelo-data-prepare --force-refresh
run_simulation
```

### Advanced Usage

#### Clearing Cache

```bash
# Clear all caches (preparation cache and data cache)
steelo-cache clear

# Clear all caches but keep recent preparation caches
steelo-cache clear --keep-recent 3
```

**Note:** The `steelo-cache clear` command clears the preparation cache, downloaded data packages cache, and the `data/` directory to ensure a completely fresh state.

#### Using Development Geo Data

```bash
# Use specific geo-data version via command line
steelo-data-prepare --geo-version 1.1.0-dev

# Or set via environment variable
export STEELO_GEO_VERSION=1.1.0-dev
steelo-data-prepare
```

#### Manual Data Management (Advanced)

**Note**: Manual data management is rarely needed. The `run_simulation` command handles all data preparation automatically.

For debugging or special cases requiring control over individual steps:

```bash
# Download specific packages
steelo-data-download --package core-data
steelo-data-download --package geo-data

# Prepare data with specific options
steelo-data-prepare --force-refresh

# Extract geo data separately
steelo-data-extract-geo

# Recreate JSON repositories
steelo-data-recreate --package core-data --output-dir ./data/repositories
```

### Step 2: Run the Simulation

Once data preparation is complete, start the simulation:

```bash
# Run simulation with default settings
run_simulation

# Run against a specific master input workbook
run_simulation --master-excel ./master_input/my_master.xlsx

# Run with custom parameters and redirect log
run_simulation --start-year 2025 --end-year 2035 > /tmp/simulation.log 2>&1
```

#### CLI Options

The full list is the `run_simulation -h` dump in [CLI Commands](cli_commands.md). The commonly used options:

**Simulation Parameters:**
- `--start-year`: Starting year for simulation (default: 2025)
- `--end-year`: Ending year for simulation (default: 2060)
- `--master-excel`: Master input workbook to prepare and run against (default: the packaged one, downloaded if needed)
- `--steelo-home`: STEELO_HOME directory holding caches and outputs (default: `~/.steelo` or `$STEELO_HOME`)
- `--log-level`: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL; default: WARNING)
- `--plot-tm` / `--plot-geo`: Also write the per-year trade maps (`plots/TM/`) and the geospatial PNGs (`plots/GEO/`); both are off by default, and the interactive viewers under `plots/interactive/` replace the trade maps
- `--run-name`: Human-readable run name shown in the interactive viewer titles (default: the `sim_<timestamp>` directory name); a name that a run under the output directory already holds gets `_2`, `_3` and so on
- `--random-seed`: Seed for the run-time RNGs shared by the plant agent, geospatial and trade LP modules (default: 42)
- `--resume-from-year`: Resume the simulation from a checkpoint at the given year

Outputs go to a fresh `sim_<timestamp>/` directory under `--output-dir` (default `$STEELO_HOME/output`); the run ends by writing its `viewer/` files there, for the [Scenario Viewer](scenario_viewer.md). Open the run with `steelo-viewer <run folder>`.

**Scenario Selection:**
- `--demand-scenario` / `--scrap-scenario`: `Scenario` rows of the "Demand and scrap availability" sheet used for steel demand and for scrap availability (default: BAU; scrap falls back to the demand scenario). Both are picked at run time from the workbook's one preparation, which holds every scenario; a name it lacks stops the run before it starts, with the names it holds
- `--grid-emissions-scenario`: Grid emissivity projection applied at run time, named as in the "Power grid emissivity" sheet without its `projection_` prefix (default: Business As Usual)
- `--peg-iron-to-steel-price` / `--iron-to-steel-price-ratio`: Floor the iron price at a share of the steel price (default ratio: 0.8)
- `--clustering` / `--no-clustering`: Cluster furnace groups to reduce trade-LP size (default: on; the former `--enable-clustering` is still accepted)
- `--clustering-scope`: How finely furnace groups that use or make hot metal, DRI or liquid iron are clustered: `plant` (default), `plant_group` or `iso3`. See [Trade Model Setup](../domain_simulation_logic/trade_model/trade_model_setup.md)
- `--hydrogen-ceiling-percentile`: Percentile of a region's LCOH used as the regional hydrogen price cap (default: 100, which disables the cap). See [Hydrogen Costs](../domain_simulation_logic/geospatial_model/priority_location_selection.md#hydrogen-costs)
- `--intraregional-trade` / `--no-intraregional-trade`: Allow or disallow hydrogen imports between linked regions (default: off)

**Data Files (usually handled automatically via caching):**
- `--baseload-power-sim-dir`: BOA-generated baseload power simulation output directory

**Caching Options:**
- `--cache-stats`: Show cache statistics and exit
- `--clear-cache`: Clear the preparation cache and exit (use `steelo-cache list` and `steelo-cache clear --keep-recent N` for finer control)
- `--force-refresh`: Force fresh data preparation (bypass cache)
- `--no-cache`: Disable caching for this run

**Capacity Policy:**
- `--enable-capacity-policy`: Enable China's capacity-replacement policy (default: disabled)
- `--credit-validity-years N`: Years a capacity-pool credit may sit banked before it expires; requires `--enable-capacity-policy` (default: no expiry)

**Carbon border:**
- `--cbam-rebate`: Rebate the carbon already paid on exports leaving a carbon border region (default: disabled)

### Step 3: Monitor Progress

In a separate terminal, monitor the simulation progress:

```bash
# Watch the log file in real-time
tail -f /tmp/simulation.log
```

The simulation will output progress updates, including:
- Current simulation year
- Plant capacity changes
- Technology transitions
- Trade allocations
- Cost calculations

## Method 3: Django Web Interface

The web interface provides a user-friendly way to configure and run simulations with real-time progress tracking.

### Quick Start

```bash
# Initial setup (only once)
uv run src/django/manage.py migrate

# Start the web server
uv run src/django/manage.py runserver
```

Then open http://localhost:8000: the Simulations page starts a background worker (Step 4). Prepare data from a master Excel workbook (Step 2), then create and run a simulation (Step 5).

### Detailed Steps

#### Step 1: Create the Database

```bash
uv run src/django/manage.py migrate
```

#### Step 2: Prepare Data

Data is prepared in the app from a master Excel workbook, once the server runs (Step 3) with a worker (Step 4):

1. Click **Master Excel** in the navigation bar. **Upload New** adds a workbook; **Download Template** downloads the packaged master to start from.
2. On the workbook's page, once it has passed validation, click **Prepare Data with This Master Excel**.

The preparation runs in a background worker; without one, the app refuses to start it. Once it is ready, it appears in the run form's "Master Input Selection" dropdown.

#### Step 3: Start the Django Development Server

```bash
# Start the web server on http://localhost:8000
uv run src/django/manage.py runserver
```

#### Step 4: Background Workers

Simulations and data preparations run in background workers, which the app starts itself. When the Simulations page (or the first-time set-up page) first loads in a browser session and no worker exists, the app starts one: without a prompt when the machine has memory to spare, and after its "Insufficient Memory Warning" ("Proceed Anyway") when it reports no capacity. The worker panel on the Simulations page adds more. A `manage.py steelo_worker` started by hand does not work: a worker needs the registration and launch token the app creates for it.

The workers keep the web interface responsive during long-running simulations.

#### Step 5: Create and Run a Simulation

1. Open your browser and navigate to http://localhost:8000
2. Click "New Simulation" to create a new model run
3. Configure simulation parameters:
   - Set start and end years
   - Under "Data Input", pick the data preparation, then its steel demand and scrap availability scenarios, which come from that preparation; the scrap scenario's "Same as demand" follows the steel demand scenario
   - Configure technology availability; runs exclude BOF from greenfield builds, and the form does not change that
   - Set economic parameters
   - Under "Policy Settings", set the carbon cost emissions boundary, the carbon border export rebates and the "Capacity-replacement policy (China)" switch, whose levers show below it while it is on; the switch needs a data preparation holding the capacity pool data
   - A (?) beside a setting opens a fuller explanation of what it does
4. Click "Create Simulation"; a name another run already holds gets `_2`, `_3` and so on
5. On the model run detail page, click "Run Simulation"
6. Monitor progress in real-time on the web interface
7. Once the run has finished, "Open in viewer" on its page opens it in the [Scenario Viewer](scenario_viewer.md); "Scenario viewer" in the navigation bar shows every finished run

### Managing Data Packages

When updating geo-data or core-data packages (e.g., upgrading geo-data.zip to a new version), you may need to clean up old DataPreparation and DataPackage objects from the database.

#### Option 1: Using Django Shell

```bash
# Open the Django shell
uv run src/django/manage.py shell

# In the shell, remove old data packages
from steeloweb.models import DataPackage, DataPreparation

# Delete all old data preparations
DataPreparation.objects.all().delete()

# Delete all old data packages
DataPackage.objects.all().delete()

# Exit the shell
exit()
```

#### Option 2: Using the Management Command

A `cleanup_data_packages` management command is available for cleaning up old data packages and their associated files:

```bash
# Delete all data packages and preparations (including files)
uv run src/django/manage.py cleanup_data_packages

# Keep only the latest versions of each package type
uv run src/django/manage.py cleanup_data_packages --keep-latest

# Preview what would be deleted without actually deleting
uv run src/django/manage.py cleanup_data_packages --dry-run

# Delete database records only, keep files in media directory
uv run src/django/manage.py cleanup_data_packages --keep-files
```

The command options:
- `--keep-latest`: Keeps the most recent version of each package type while removing older versions
- `--dry-run`: Shows what would be deleted without making any changes
- `--keep-files`: Removes database records but preserves the actual data files in the media directory

After cleaning up, prepare the data again from the master Excel page (Step 2), which downloads the package versions the code's data manifest names.

## Output Files

Each run writes into its own folder: `$STEELO_HOME/output/sim_<timestamp>/` (or the folder under `--output-dir`) from the CLI, and `model_outputs/run_<id>/` in the app's media folder from the web interface. In it:

- **CSV files**: Detailed simulation results in `TM/`
- **Plots**: Visualization charts in `plots/`
  - Cost curves
  - Capacity development
  - Trade flows
  - Geographic distributions
  - Greenfield plant status charts, maps and per-plant CSV (`plots/greenfield/`)
  - Capacity pool charts on policy-ON runs (`plots/capacity_pool/`)
- **Fleet motions**: `data/pam_motions.csv` on every run, plus the `data/policy/` CSVs on policy-ON runs
- **Run settings**: `simulation_config.json` and `preparation_metadata.json`, written before the run starts
- **Scenario viewer files**: `viewer/`, written at the end of the run, which the [Scenario Viewer](scenario_viewer.md) reads; `steelo-viewer regenerate <run folder>` writes them again. The files are listed in [Outputs and postprocessing](../domain_simulation_logic/overarching_elements/outputs_and_postprocessing.md#scenario-viewer-files-viewer)

## Troubleshooting

### Common Issues

1. **"No data preparations available" error**
   - Prepare data first from the master Excel page ("Prepare Data with This Master Excel", Step 2); a worker must be running
   - Check that S3 credentials are configured if using private buckets

2. **Empty plants.json file (0 plants)**
   - This usually indicates cached data from before a bug fix
   - Solution: Force fresh data preparation
   ```bash
   steelo-cache clear
   run_simulation --force-refresh
   ```
   - The cache system now includes version tracking to prevent this

3. **Simulation hangs or crashes**
   - Check available memory (simulations can be memory-intensive)
   - Examine logs for specific error messages
   - Ensure all required data files are present

4. **Missing plots or visualizations**
   - Verify that geo-data was properly extracted
   - Check that matplotlib backend is configured correctly
   - Look for errors in the simulation log

### Debugging Tips

- Use `--log-level DEBUG` flag with CLI commands for verbose output
- Check Django logs in the terminal running `runserver`
- Examine background worker output for task execution details
- Review generated CSV files for intermediate results

## Configuration

### Environment Variables

Key environment variables that affect simulation behavior:

- `STEELO_HOME`: Base directory for steelo data (default: `~/.steelo`)
  - Contains: `preparation_cache/`, `output/`, `data_cache/`
  - All simulation outputs and caches are stored here
- `DEVELOPMENT`: Set to `true` for development mode
- `MPLBACKEND`: Matplotlib backend (set to `Agg` for headless environments)

### Simulation Parameters

Key parameters you can configure:
- **Time Period**: Start and end years for the simulation
- **Technology Constraints**: Which technologies are allowed and when
- **Economic Factors**: Carbon tax, capital costs, trade scenarios
- **Geographic Constraints**: Land use, infrastructure availability

:::
