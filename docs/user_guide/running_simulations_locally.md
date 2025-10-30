# Running Steel Model Simulations Locally

This guide explains how to run the steel model simulation during local development and how to integrate new data sources into the simulation pipeline.

:::{only} public
## Quick Start (CLI)

After installing the Steel Model package, run a simulation from the command line:

```bash
run_simulation --start-year 2025 --end-year 2030 --output-dir ./simulation_outputs
```

Common options:
- `--start-year` / `--end-year`: define the scenario horizon.
- `--config-file`: load a saved configuration.
- `--log-level`: control verbosity (`INFO`, `DEBUG`, etc.).

The CLI writes metrics, logs, and artefacts to the chosen output directory. Review the [Configuration](configuration.md) guide for a comprehensive list of parameters and environment variables.

## Custom Data Overview

To experiment with bespoke datasets:

1. Prepare files that conform to the schemas referenced in the configuration guide.
2. Point the CLI at your resources, for example:

   ```bash
   run_simulation \
     --plants-json ./my_data/plants.json \
     --demand-xlsx ./my_data/demand.xlsx \
     --output-dir ./custom_run
   ```

3. Inspect the generated reports (`metrics.json`, plots, logs) under your output directory.

For notebook or service integrations, see the [Command-Line Entrypoints](commandline_entrypoints.md) reference.
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
from steelo.simulation import SimulationConfig
from steelo.simulation_runner import create_simulation_runner
from steelo.domain import Year

config = SimulationConfig.from_data_directory(
    start_year=Year(2025),
    end_year=Year(2030),
    data_dir=Path("./data"),
    output_dir=Path("./test_outputs")
)

runner = create_simulation_runner(config)
results = runner.run()

# Access results
print(f"Final steel price: {results['price']}")
print(f"Total production: {results['production']}")
```

### Custom Paths Example

```python
config = SimulationConfig(
    # Custom output paths
    output_dir=Path("./custom_outputs"),
    plots_dir=Path("./custom_outputs/plots"),
    
    # Custom input data
    plants_json_path=Path("./my_data/plants.json"),
    demand_center_xlsx=Path("./my_data/demand.xlsx"),
    cost_of_x_csv=Path("./my_data/cost_of_x.json"),
    
    # Time and parameters
    start_year=Year(2025),
    end_year=Year(2050),
    scrap_generation_scenario="high_recycling",
)
```

### Technology Constraints Example

```python
from steelo.simulation_types import get_default_technology_settings, TechnologySettings

# Create technology settings with specific constraints
tech_settings = get_default_technology_settings()

# Ban blast furnaces by setting allowed=False
tech_settings['BF'] = TechnologySettings(
    allowed=False,
    from_year=2025,
    to_year=None
)

# Allow hydrogen DRI only from 2030
tech_settings['DRIH2'] = TechnologySettings(
    allowed=True,
    from_year=2030,
    to_year=None
)

# Disable certain technologies
tech_settings['ESF'] = TechnologySettings(
    allowed=False,
    from_year=2025,
    to_year=None
)
tech_settings['MOE'] = TechnologySettings(
    allowed=False,
    from_year=2025,
    to_year=None
)

config = SimulationConfig(
    start_year=Year(2025),
    end_year=Year(2040),
    technology_settings=tech_settings,
)
```

For more examples, see `examples/run_simulation_example.py`.

## Caching System

The CLI implements a content-based caching system that significantly speeds up repeated simulations:

### How It Works

1. **Content Hashing**: The master Excel file is hashed using SHA256 to create a unique cache key
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

# Clear old caches but keep recent ones
run_simulation --cache-clear --keep-recent 3

# Force fresh preparation (bypass cache)
run_simulation --force-refresh

# Disable caching entirely
run_simulation --no-cache
```

### Cache Versioning

The cache system includes automatic version tracking. When the code that processes data changes, old caches are automatically invalidated. This ensures you always get correctly processed data without manual intervention.

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
│   │   └── metadata.json            # Cache metadata
│   └── prep_e5f6g7h8/               # Another cached preparation
├── output/                          # Simulation outputs
│   ├── sim_20240726_143052/        # Timestamped simulation
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

# Run with custom output directory
run_simulation --output-dir ./my_simulation_outputs

# Run with custom parameters and redirect log
run_simulation --start-year 2025 --end-year 2035 --output-dir ./outputs > /tmp/simulation.log 2>&1
```

#### CLI Options

**Simulation Parameters:**
- `--start-year`: Starting year for simulation (default: 2025)
- `--end-year`: Ending year for simulation (default: 2050)
- `--output-dir`: Base output directory for results (default: $STEELO_HOME/output)
- `--log-level`: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL; default: WARNING)

**Data Files (usually handled automatically via caching):**
- `--plants-json`: Path to plants JSON file
- `--demand-excel`: Path to demand Excel file  
- `--location-csv`: Path to location CSV file
- `--cost-of-x-csv`: Path to cost of x JSON file

**Caching Options:**
- `--cache-stats`: Show cache statistics and exit
- `--cache-list`: List all cached preparations and exit
- `--cache-clear`: Clear cache (use with --keep-recent N to keep some)
- `--force-refresh`: Force fresh data preparation (bypass cache)
- `--no-cache`: Disable caching for this run

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

# Prepare data
uv run src/django/manage.py prepare_default_data

# Start services
uv run src/django/manage.py runserver
uv run src/django/manage.py db_worker  # in separate terminal
```

### Detailed Steps

#### Step 1: Create the Database

```bash
uv run src/django/manage.py migrate
```

#### Step 2: Prepare Default Data

Prepare the data files needed for simulations:

```bash
# Standard preparation
uv run src/django/manage.py prepare_default_data

# Use development geo data
uv run src/django/manage.py prepare_default_data --geo-version 1.1.0-dev

# Or via environment variable
export STEELO_GEO_VERSION=1.1.0-dev
uv run src/django/manage.py prepare_default_data
```

This command will:
- Download the master-input Excel file from S3
- Download core-data and geo-data packages from S3
- Extract data from the master Excel file
- Copy files from core-data package
- Generate derived files (like plant_groups.json)
- Extract geo-data files
- Create all fixture files in `data/fixtures/`

**Options:**
- `--name`: Name for the data preparation (default: "Default Data")
- `--force`: Force re-preparation even if data exists
- `--geo-version`: Specific version of geo-data to use (e.g., '1.1.0-dev')
- `--master-excel-id`: ID of a MasterExcelFile to use (if you've uploaded one)
- `--quiet`: Hide detailed output (only show summary)
- `--no-check-files`: Skip file existence checking

Note: The master Excel file is now mandatory for data preparation. The command uses a centralized data preparation service that ensures consistent file tracking across all data preparation methods.

#### Step 3: Start the Django Development Server

```bash
# Start the web server on http://localhost:8000
uv run src/django/manage.py runserver
```

#### Step 4: Start the Background Worker

In a separate terminal, start the task worker that handles simulation execution:

```bash
# Start the background worker for running simulations
uv run src/django/manage.py db_worker
```

The worker ensures the web interface remains responsive during long-running simulations.

#### Step 5: Create and Run a Simulation

1. Open your browser and navigate to http://localhost:8000
2. Click "New Simulation" to create a new model run
3. Configure simulation parameters:
   - Set start and end years
   - Choose scenarios (demand, scrap generation)
   - Configure technology availability
   - Set economic parameters
4. Click "Create Model Run"
5. On the model run detail page, click "Run Simulation"
6. Monitor progress in real-time on the web interface

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

After cleaning up, run `prepare_default_data` again to download the latest versions.

## Output Files

Both methods generate output files in the `outputs/` directory:

- **CSV files**: Detailed simulation results in `outputs/TM/`
- **Plots**: Visualization charts in `outputs/plots/`
  - Cost curves
  - Capacity development
  - Trade flows
  - Geographic distributions

## Troubleshooting

### Common Issues

1. **"No data preparations available" error**
   - Run `uv run src/django/manage.py prepare_default_data` first
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
