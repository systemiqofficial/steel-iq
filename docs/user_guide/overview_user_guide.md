# User Guide Overview

Welcome to the Steel Model User Guide. This section contains comprehensive documentation for users and developers working with the Steel Model simulation system.

## Contents

### [Installation Guide](installation_guide.md)
Step-by-step installation instructions for developers. Covers:
- **Basic installation**: Clone repository, set up virtual environment with uv, install dependencies
- **Running tests**: pytest, coverage reports, wind/PV tests
- **Static type checking**: mypy configuration and usage
- **Managing dependencies**: Adding, updating, and upgrading packages with uv
- **Building the package**: Creating distribution packages
- **Jupyter notebooks**: Running interactive analysis notebooks
- **Standalone application**: Building Electron apps via GitHub Actions
- **System requirements**: CBC solver installation for different platforms

### [Running Simulations Locally](running_simulations_locally.md)
Complete guide to running steel model simulations on your local machine. Covers:
- **Three execution methods**: Programmatic (Python/Notebook), Command-Line Interface (CLI), and Django Web Interface
- **Data pipeline architecture**: From S3 storage through local caching to simulation execution
- **Caching system**: Content-based caching for faster repeated simulations
- **Configuration examples**: Technology constraints, custom paths, and simulation parameters
- **Troubleshooting**: Common issues and debugging tips
- **Output files**: Understanding simulation results and visualizations

### [CLI Commands](cli_commands.md)
Reference documentation for all command-line interface tools:
- **run_simulation**: Main command to run simulations with automatic data preparation and caching
- **list-binaries**: View available Steel Model standalone binaries on S3
- **download-binaries**: Download specific builds of the Electron app
- **download-latest**: Get the latest version of the Electron app for your platform

Each command includes usage examples, options, and practical tips.

### [Command-Line Entrypoints](commandline_entrypoints.md)
Detailed reference for programmatic access to CLI functionality. Covers:
- Python functions behind CLI commands
- Integration patterns for custom workflows
- Examples of programmatic usage
- Advanced configuration options

### [Configuration](configuration.md)
Comprehensive guide to configuring the Steel Model simulation:
- **SimulationConfig**: Central configuration object for all simulation parameters
- **Path configuration**: Custom data sources and output directories
- **Technology settings**: Controlling which technologies are available and when
- **Economic parameters**: Carbon pricing, capital costs, discount rates
- **Geographic constraints**: Land use, infrastructure, and renewable energy
- **Environment variables**: System-wide configuration options

### [Logging Guide](LOGGING_GUIDE.md)
Understanding and controlling simulation logging:
- **Log levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL
- **Configuring loggers**: Module-specific logging control
- **Output destinations**: Console, file, and structured logging
- **Debugging workflows**: Using logs to troubleshoot issues
- **Performance logging**: Tracking simulation performance metrics

## Quick Start

### For Developers (from source)

See the [Installation Guide](installation_guide.md) for complete setup instructions:

1. **Clone and set up**:
   ```bash
   git clone git@github.com:systemiqofficial/steel-model.git
   cd steel-model
   uv venv -p python3.13
   source .venv/bin/activate
   uv sync
   ```

2. **Run tests**:
   ```bash
   pytest
   ```

3. **Run a simulation**:
   ```bash
   run_simulation
   ```

### For Package Users

1. **Install the package**:
   ```bash
   uv add steel-model
   ```

2. **Prepare data** (automatic):
   ```bash
   run_simulation
   ```
   The `run_simulation` command automatically downloads and prepares all required data.

3. **Run a simulation**:
   ```bash
   run_simulation --start-year 2025 --end-year 2030
   ```

### Common Workflows

#### Running a Quick Test
```bash
# Short simulation for testing
run_simulation --start-year 2025 --end-year 2027 --log-level INFO
```

#### Using Custom Data
```bash
# Prepare data with your own master Excel
steelo-data-prepare --master-excel path/to/your/master_input.xlsx

# Run simulation
run_simulation
```

#### Web Interface
```bash
# Set up Django (first time only)
cd src/django
python manage.py migrate
python manage.py prepare_default_data

# Start services
python manage.py runserver          # Terminal 1
python manage.py db_worker          # Terminal 2
```

Then open http://localhost:8000 in your browser.

## Key Concepts

### Data Pipeline
The Steel Model uses a structured data flow:
1. **S3 Storage**: Raw data packages (core-data, geo-data, master Excel)
2. **Local Cache**: Downloaded packages cached in `~/.steelo/data_cache/`
3. **Data Preparation**: Transform raw data into JSON repositories
4. **Preparation Cache**: Processed data cached in `~/.steelo/preparation_cache/`
5. **Simulation**: Uses prepared fixtures for fast execution

### Caching System
- **Content-based**: Cache keys derived from master Excel file hash
- **Automatic reuse**: Same inputs = instant cache hit
- **Version tracking**: Automatic invalidation when code changes
- **Cache commands**: `steelo-cache stats`, `steelo-cache clear`, etc.

### Configuration Hierarchy
1. **Environment variables**: System-wide defaults (e.g., `STEELO_HOME`)
2. **SimulationConfig**: Explicit configuration object
3. **Command-line flags**: Override defaults for specific runs

## Related Documentation

- **[Architecture Overview](../architecture/overview_architecture.md)**: Overall system design
- **[Dashboard & UI](../dashboard/overview_dashboard.md)**: Web interface and Electron app documentation

## Getting Help

### Documentation
- **Quick reference**: See [CLI Commands](cli_commands.md) for command syntax
- **Detailed guides**: See [Running Simulations Locally](running_simulations_locally.md)
- **Configuration**: See [Configuration](configuration.md)

### Troubleshooting
Common issues and solutions are documented in:
- [Running Simulations Locally](running_simulations_locally.md) - See the Troubleshooting section
- [CLI Commands](cli_commands.md) examples

### Support
- **Issue Tracker**: Report bugs and request features
- **Documentation**: Comprehensive guides and API references
- **Community**: Share experiences and get help
