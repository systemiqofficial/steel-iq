# Data Management Overview

The Steel Model data management system provides a comprehensive solution for handling model input data, supporting both PyPI installations and bundled Electron apps.

## Contents

- **[CLI Commands](cli_commands.md)** - Command-line interface reference
- **[Python API](python_api.md)** - Programmatic usage and examples
- **[Data Packages](data_packages.md)** - Package structure and creation guide
- **[Django Data Management](django_data_management.md)** - Web UI file uploads and data preparation
- **[Adding Excel Data How-To](adding_excel_data_howto.md)** - Guide for adding new data to master Excel

## Quick Start

```bash
# Install the package
uv add steel-model

# Prepare all data files (recommended)
steelo-data-prepare

# Run a simulation
run_simulation
```

## Quick Links

### For Users
- [Download Data](cli_commands.md#steelo-data-download)
- [Prepare Data](cli_commands.md#steelo-data-prepare)
- [Common Workflows](cli_commands.md#common-workflows)

### For Developers
- [Python API Reference](python_api.md)
- [Creating Data Packages](data_packages.md#creating-a-new-package)
- [Integration Examples](python_api.md#integration-with-existing-code)

## Key Features

- **S3-based data distribution**: Large datasets hosted on S3, downloaded on demand
- **Local caching**: Downloaded data cached locally with integrity verification
- **Excel validation**: Validate user-provided Excel files before processing
- **JSON repository generation**: Convert Excel/CSV data to optimized JSON format
- **Version management**: Track and manage different versions of data packages

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   S3 Storage    │────▶│  Data Manager    │────▶│ Local Cache     │
│  (ZIP files)    │     │  (Download/Cache)│     │ ~/.steelo/data  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                │
                                ▼
                 ┌────────────────────────────────┐
                 │   DataPreparationService       │
                 │ (Unified preparation pipeline) │
                 └────────────────────────────────┘
                          │            │
                          ▼            ▼
                  ┌──────────────┐  ┌──────────────┐
                  │ Master Excel │  │ Core/Geo Data│
                  │  Extraction  │  │   Copying    │
                  └──────────────┘  └──────────────┘
                          │            │
                          ▼            ▼
                        ┌──────────────────┐
                        │ Prepared Fixtures│
                        │   (Model Input)  │
                        └──────────────────┘
```

## Data Flow

1. **Package Download**: Master Excel and data packages downloaded from S3
2. **Extraction**: ZIP files extracted, checksums verified
3. **Data Preparation**: Unified preparation process:
   - Extract data from master Excel (carbon costs, tariffs, etc.)
   - Copy core data files (plants.csv, cost_of_x.json, etc.)
   - Generate derived files (plant_groups.json from plants.json)
   - Extract geo data files (terrain, wind, solar data)
4. **Model Input**: Prepared fixtures used as input to simulation

All data preparation methods (CLI, Django web UI, and management command) now use the same centralized service, ensuring consistent file tracking and source attribution.

## Usage Modes

### PyPI Installation
Users install via pip/uv and prepare data:
```bash
uv add steel-model
steelo-data-prepare  # Downloads packages and prepares all data
```

### Electron App
Data bundled directly in the app, no download needed.

### Development
Developers can use local data files or download from S3.

## Related Documentation

- [Repository Pattern ADR](../architecture/adr/002_repository.md)
- [Configuration Management ADR](../architecture/adr/003_configuration.md)
- [Architecture Overview](../architecture/architecture.md)
- [CLI Commands](cli_commands.md)
- [Python API](python_api.md)
- [Data Packages](data_packages.md)
