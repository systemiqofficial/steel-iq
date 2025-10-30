# Configuration

The `steelo.config` module provides centralized settings management for our Python
application using Pydantic's `BaseSettings`. It is designed to:

- **Load Configuration**: Read settings from a `.env` file and environment variables.
- **Environment Precedence**: Environment variables override values from `.env` files.
- **Dynamic Source Selection**: Choose between a user-specific `.env` file and a
  project-level `.env` file based on availability.
- **Type Validation**: Ensure that settings are of the correct type and meet specified constraints.
- **Easy Access**: Allow settings to be easily imported and used throughout the application.

## Overview

The module performs the following steps to manage application settings:

1. **Determine the `.env` File Location**:
   - Checks if a `.env` file exists in the user's home directory at `~/.steelo/.env`.
   - If not found, falls back to the project's root directory at `<project_root>/.env`.

2. **Load Settings**:
   - Reads settings from the selected `.env` file.
   - Overrides these settings with any matching environment variables.

3. **Validate Settings**:
   - Uses Pydantic to validate types and values.
   - Ensures required settings are provided.
   - Applies any custom validation logic.

4. **Provide Access to Settings**:
   - Exposes a `settings` object that can be imported and used throughout the application.

## Usage

To use the settings in your application, simply import the `settings` object:

```python
from steelo.config import settings

# Example usage
print(settings.project_root)
```

This allows you to access configuration values like, `project_root`, etc.

## Settings Fields

The `Settings` class defines the following configuration fields:

- **`project_root`** (`Path`):
  - Description: The root directory of the project.
  - Default: Automatically set to the parent directory of the module's parent (`ROOT_DIR`).

- **`root`** (`DirectoryPath`):
  - Description: The directory where steelo-specific files are stored.
  - Default: `~/.steelo`
  - Environment Variable: `STEELO_HOME`
  - Notes: The directory is created if it does not exist.
  - Structure:
    ```
    $STEELO_HOME/
    ├── preparation_cache/     # Cached data preparations
    │   ├── index.json        # Fast lookup index
    │   └── prep_<hash>/      # Individual cache entries
    ├── output/               # Simulation outputs
    │   └── sim_<timestamp>/  # Individual simulation runs
    ├── data_cache/           # Downloaded S3 packages
    ├── data/                 # Symlink to latest preparation
    └── output_latest/        # Symlink to latest output
    ```

## Custom Validation

- **`root_must_exist`**:
  - Purpose: Ensures that the `root` directory exists by creating it if necessary.
  - Behavior: Invoked before assignment to the `root` field.

## Customizing Settings Sources

The `Settings` class customizes the order and source of configuration values by overriding the `settings_customise_sources` method:

1. **Initialization Arguments**:
   - Values provided directly when instantiating the `Settings` class.

2. **Environment Variables**:
   - Variables from the system environment.

3. **`.env` File**:
   - Checks for `~/.steelo/.env`.
   - If not found, uses `<project_root>/.env`.

4. **File Secret Settings**:
   - Not utilized in this configuration.

This custom source order allows for flexible configuration management, prioritizing user-specific settings over project defaults.

## Error Handling

If validation fails (e.g., required settings are missing), the module:

- Prints a validation error message using `rich.print`.
- Exits the application with a status code of `1`.

## Example `.env` File

An example of what the `.env` file might contain:

```dotenv
# ~/.steelo/.env or <project_root>/.env
STEELO_HOME=/custom/path/to/steelo
```

## Backward Compatibility

For backward compatibility, the system automatically creates symlinks in the project root:
- `<project_root>/data/` → `$STEELO_HOME/data` (latest preparation)
- `<project_root>/output/` → `$STEELO_HOME/output_latest` (latest simulation)

This ensures existing scripts that reference these paths continue to work without modification.
