# Python API Reference

The data management module provides a Python API for programmatic access to all functionality.

## Core Classes

### DataManager

Main class for downloading and managing data packages.

```python
from steelo.data import DataManager

# Initialize with defaults
manager = DataManager()

# Custom cache directory
manager = DataManager(cache_dir=Path("/custom/cache"))

# Offline mode (no downloads)
manager = DataManager(offline_mode=True)

# Custom manifest
manager = DataManager(manifest_path=Path("custom_manifest.json"))
```

**Methods:**

```python
# Download all required packages
manager.download_required_data(force=False)

# Download specific package
manager.download_package("core-data", force=False)

# Get path to downloaded package
package_path = manager.get_package_path("core-data")

# List all packages with status
packages = manager.list_packages()

# Verify integrity
results = manager.verify_data_integrity()

# Clear cache
manager.clear_cache()  # All packages
manager.clear_cache("core-data")  # Specific package
```

### DataPreparationService

Unified service for preparing all data files needed for simulation. This is the recommended API for data preparation.

```python
from steelo.data import DataPreparationService, DataManager

# Initialize service
manager = DataManager()
service = DataPreparationService(data_manager=manager)

# Prepare all data files
result = service.prepare_data(
    output_dir=Path("data/fixtures"),
    master_excel_path=None,  # Will download from S3
    skip_existing=True,
    verbose=True
)

# Access preparation results
print(f"Total files prepared: {len(result.files)}")
print(f"Total time: {result.total_duration:.2f} seconds")

# Files are grouped by source
files_by_source = result.get_files_by_source()
for source, files in files_by_source.items():
    print(f"\n{source.value}: {len(files)} files")
    for file in files[:5]:  # Show first 5
        print(f"  - {file.filename}")
```

**Return Value:**
The `prepare_data()` method returns a `PreparationResult` object with:
- `files`: List of `PreparedFile` objects with metadata
- `steps`: List of high-level preparation steps with timing
- `total_duration`: Total preparation time in seconds
- `output_directory`: Path where files were prepared

### DataRecreator

Recreates JSON repositories from downloaded packages.

```python
from steelo.data import DataRecreator, DataManager

# Initialize with data manager
manager = DataManager()
recreator = DataRecreator(manager)

# Recreate from specific package
paths = recreator.recreate_from_package(
    "core-data",
    output_dir=Path("./output"),
    force_download=False
)

# Recreate from all packages
all_paths = recreator.recreate_all_packages(Path("./output"))
```

### ExcelValidator

Validates Excel files before conversion.

```python
from steelo.data.validation import ExcelValidator

# Initialize validator
validator = ExcelValidator(strict_mode=False)

# Validate plants file
result = validator.validate_plants_file(Path("plants.xlsx"))
if result.valid:
    print("File is valid")
else:
    for error in result.errors:
        print(f"Error in {error['field']}: {error['message']}")

# Validate all files in directory
results = validator.validate_all_files(Path("./excel_files"))

# Convert validated files to JSON
repo_paths = validator.convert_to_repositories(
    Path("./excel_files"),
    Path("./json_output")
)
```

### DataManifest

Manages package metadata.

```python
from steelo.data import DataManifest, DataPackage

# Load manifest
manifest = DataManifest.load(Path("manifest.json"))

# Add new package
package = DataPackage(
    name="custom-data",
    version="1.0.0",
    url="https://example.com/data.zip",
    size_mb=10.0,
    checksum="sha256_hash",
    description="Custom data package",
    required=False,
    files=["data1.csv", "data2.xlsx"]
)
manifest.add_package(package)

# Save manifest
manifest.save(Path("manifest.json"))

# Query packages
required = manifest.get_required_packages()
package = manifest.get_package("core-data")
```

## Usage Examples

### Basic Download and Recreate

```python
from pathlib import Path
from steelo.data import DataManager, DataRecreator

# Download data
manager = DataManager()
manager.download_required_data()

# Recreate JSON repositories
recreator = DataRecreator(manager)
output_dir = Path("./data/repositories")
paths = recreator.recreate_from_package("core-data", output_dir)

print(f"Created repositories: {paths}")
```

### Validate User Excel Files

```python
from pathlib import Path
from steelo.data.validation import ExcelValidator, ValidationResult

def validate_user_upload(file_path: Path) -> ValidationResult:
    """Validate user-uploaded Excel file."""
    validator = ExcelValidator(strict_mode=True)
    
    if "plant" in file_path.stem.lower():
        return validator.validate_plants_file(file_path)
    elif "demand" in file_path.stem.lower():
        return validator.validate_demand_file(file_path)
    else:
        return ValidationResult(
            valid=False,
            errors=[{"field": "file", "message": "Unknown file type"}]
        )

# Use in Django view
result = validate_user_upload(uploaded_file_path)
if not result.valid:
    # Return errors to user
    return {"errors": result.errors}
```

### Custom Data Pipeline

```python
from pathlib import Path
from steelo.data import DataManager, DataRecreator
from steelo.simulation import SimulationConfig

class DataPipeline:
    """Custom data pipeline for simulations."""
    
    def __init__(self, cache_dir: Path | None = None):
        self.manager = DataManager(cache_dir=cache_dir)
        self.recreator = DataRecreator(self.manager)
        
    def prepare_simulation_data(self, output_dir: Path) -> SimulationConfig:
        """Prepare all data for simulation."""
        # Download if needed
        self.manager.download_required_data()
        
        # Recreate repositories
        paths = self.recreator.recreate_from_package(
            "core-data",
            output_dir / "core"
        )
        
        # Create simulation config
        return SimulationConfig(
            plants_json_path=paths["plants"],
            demand_centers_json_path=paths["demand_centers"],
            suppliers_json_path=paths["suppliers"],
            # ... other paths
        )
```

### Error Handling

```python
from steelo.data import (
    DataManager,
    DataDownloadError,
    DataValidationError,
    DataIntegrityError
)

manager = DataManager()

try:
    manager.download_package("core-data")
except DataDownloadError as e:
    print(f"Download failed: {e}")
    # Handle download errors (network, permissions, etc.)
except DataIntegrityError as e:
    print(f"Integrity check failed: {e}")
    # Handle corrupted downloads
except Exception as e:
    print(f"Unexpected error: {e}")
```

### Integration with Existing Code

```python
from steelo.data import DataManager
from steelo.config import settings

# Override settings to use downloaded data
manager = DataManager()
package_path = manager.get_package_path("core-data")

# Temporarily override settings
original_path = settings.steel_plants_csv
try:
    settings.steel_plants_csv = package_path / "steel_plants.csv"
    # Run existing code that uses settings
    from steelo.data.recreation_functions import recreate_plants_data
    from steelo.adapters.dataprocessing.excel_reader import read_plants
    
    # First read plants from CSV
    plants = read_plants(
        steel_plants_csv=package_path / "steel_plants.csv",
        # ... other required parameters
    )
    
    # Then write them to JSON
    recreate_plants_data(plants=plants, json_path=output_path)
finally:
    # Restore original settings
    settings.steel_plants_csv = original_path
```

## Exceptions

### DataDownloadError
Raised when package download fails.

```python
try:
    manager.download_package("missing-package")
except DataDownloadError as e:
    print(f"Download error: {e}")
```

### DataValidationError
Raised when data validation fails.

```python
try:
    validator.convert_to_repositories(input_dir, output_dir)
except DataValidationError as e:
    print(f"Validation failed: {e}")
    for error in e.errors:
        print(f"  - {error}")
```

### DataIntegrityError
Raised when data integrity check fails.

```python
try:
    manager.verify_data_integrity()
except DataIntegrityError as e:
    print(f"Integrity check failed: {e}")
```