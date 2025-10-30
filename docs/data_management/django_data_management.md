# Django Data Management System

This document describes the hybrid data management system for running simulations through the Django interface.

## Overview

The system supports both S3-hosted data packages and locally uploaded archives, with version management for different environments (production/development).

## Key Components

### Models

1. **DataPackage**: Stores references to data archives (ZIP files)
   - Can be from S3 (URL reference) or local upload (file)
   - Tracks version, checksum, and size
   - Types: `core-data` and `geo-data`

2. **DataPreparation**: Manages the extraction and preparation process
   - Links core-data and geo-data packages
   - Tracks status (pending → downloading → extracting → preparing → ready)
   - Stores the path to prepared data
   - Includes preparation logs

3. **ModelRun**: Updated to use DataPreparation
   - Can reference a DataPreparation for its data source
   - Falls back to legacy Repository model if no DataPreparation

## Usage

### Quick Start (Recommended)

The simplest approach uses the `prepare_default_data` management command:

```bash
# Standard preparation (uses default geo-data version)
uv run src/django/manage.py prepare_default_data

# Use development geo-data version
uv run src/django/manage.py prepare_default_data --geo-version 1.1.0-dev

# Or via environment variable
export STEELO_GEO_VERSION=1.1.0-dev
uv run src/django/manage.py prepare_default_data
```

### Manual Data Package Management (Advanced)

#### Step 1: Import Data Packages

##### From S3:
```bash
python manage.py import_data_packages --from-s3
```

##### From Local Files:
```bash
python manage.py import_data_packages \
  --core-data /path/to/core-data-v1.0.3.zip \
  --geo-data /path/to/geo-data-v1.0.6.zip \
  --package-version 1.0.6
```

#### Step 2: Create Data Preparation

1. Go to Django Admin → Data Preparations
2. Click "Add Data Preparation"
3. Give it a name (e.g., "Production Data v1.0.6")
4. Select core-data and geo-data packages (specific versions if multiple exist)
5. Save

#### Step 3: Prepare Data

1. In the Data Preparations list, select your preparation
2. Choose "Prepare selected data" from the Actions dropdown
3. Click "Go"
4. The preparation will run in the background
5. Monitor progress in the preparation detail view

#### Step 4: Run Simulation

1. Create a new Model Run
2. Select your Data Preparation
3. Configure other parameters as needed
4. Save and run

## Background Tasks

The system uses Django Tasks for background processing:
- Data preparation runs asynchronously
- Progress is logged to the preparation record
- Errors are captured and displayed

## File Structure

Prepared data is organized as:
```
media/prepared_data/prep_<id>/
├── data/
│   ├── fixtures/          # JSON repositories and raw files
│   │   ├── plants.json
│   │   ├── demand_centers.json
│   │   ├── cost_of_x.json
│   │   └── ...
│   ├── atlite/           # Geo data files
│   ├── outputs/GEO/      # Geo output files
│   └── ...
```

## Environment Variables

Set these in Django's `.env` file:
```
MPLBACKEND=Agg            # Non-interactive matplotlib backend
```

## Troubleshooting

1. **Import errors**: The system uses lazy imports to avoid circular dependencies
2. **File not found**: Check that data files exist in the expected locations
3. **Matplotlib errors**: Set MPLBACKEND=Agg for headless environments

## Test Script

A test script is provided to demonstrate the workflow:
```bash
cd src/django
python test_data_preparation.py
```

## Geo-Data Versioning

The system supports multiple versions of geo-data packages for different environments:

### Configuration

1. **Command Line**: Use `--geo-version` flag
   ```bash
   uv run src/django/manage.py prepare_default_data --geo-version 1.1.0-dev
   ```

2. **Environment Variable**: Set `STEELO_GEO_VERSION`
   ```bash
   export STEELO_GEO_VERSION=1.1.0-dev
   uv run src/django/manage.py prepare_default_data
   ```

3. **Manifest Tags**: Packages can be tagged in `manifest.json`
   - `"default"`: Default version when none specified
   - `"production"`: Production-ready version
   - `"development"`: Development version with new features

### Version Selection Logic

1. If a specific version is requested, that exact version is used
2. If no version is specified:
   - First looks for package with `"default"` tag
   - Otherwise uses the latest version (lexicographically)

## Implementation Summary

The hybrid data management system successfully addresses the requirements:

1. **S3 Integration**: DataPackages can reference S3 URLs for cloud-based distribution
2. **Local File Support**: Users can upload ZIP archives directly through Django
3. **Version Tracking**: Each package and preparation is versioned
4. **Multi-Version Support**: Different geo-data versions for production/development
5. **Background Processing**: Data preparation runs asynchronously via django-tasks
6. **Status Monitoring**: Detailed logs and status tracking for debugging
7. **Backwards Compatible**: Existing ModelRuns continue to work with the Repository model

## Future Improvements

1. Add progress percentage to DataPreparation
2. Support incremental updates
3. Add data validation before preparation
4. Implement automatic cleanup of old preparations
5. Bundle data packages with Electron app
6. Add checksums verification
7. Support partial extraction for large geo-data files