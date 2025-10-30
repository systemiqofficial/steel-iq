# Data Package Specification

This document describes the structure and requirements for Steel Model data packages.

## Package Structure

Data packages are ZIP files containing model input data in various formats. Each package must include:

1. **Data files**: Excel, CSV, PKL files with model inputs
2. **Manifest entry**: Metadata in the global manifest.json

## Package Types

### Core Data Package
Contains essential model data required for basic simulations.

**Expected files:**
- `steel_plants.csv`: Plant locations and capacities
- `technology_lcop.csv`: Technology cost data
- `demand_centers.xlsx`: Demand center data with projections
- `tariffs.xlsx`: Trade tariff information
- `locations.csv`: Geographic location reference data
- `gravity_distances.pkl`: Pre-calculated distance matrix

### Geo Data Package
Contains geospatial data for location analysis.

**Expected files:**
- `global_grid.parquet`: Global grid with ISO3 codes
- `country_boundaries.geojson`: Country boundary polygons
- `infrastructure_costs.csv`: Infrastructure cost data by location
- `land_use.tif`: Land use raster data
- `elevation.tif`: Elevation data
- `slope.tif`: Slope calculations

### Business Cases Package
Contains technology and business case data.

**Expected files:**
- `business_cases.xlsx`: Dynamic business case definitions
- `carbon_costs.xlsx`: Carbon cost projections
- `energy_prices.xlsx`: Regional energy price data
- `input_costs.xlsx`: Raw material cost data

### User Templates Package
Excel templates for user data input.

**Expected files:**
- `templates/plants_template.xlsx`
- `templates/demand_template.xlsx`
- `templates/suppliers_template.xlsx`
- `templates/tariffs_template.xlsx`

## Manifest Format

Each package must have an entry in `manifest.json`:

```json
{
  "name": "package-name",
  "version": "1.0.0",
  "url": "https://steelo-data.s3.amazonaws.com/package-name-v1.0.0.zip",
  "size_mb": 50.0,
  "checksum": "sha256_checksum_here",
  "description": "Human-readable description",
  "required": true,
  "files": [
    "file1.csv",
    "file2.xlsx",
    "subfolder/file3.pkl"
  ]
}
```

**Fields:**
- `name`: Unique package identifier (lowercase, hyphens allowed)
- `version`: Semantic version (major.minor.patch)
- `url`: Full URL to download the package
- `size_mb`: Approximate size in megabytes
- `checksum`: SHA256 checksum of the ZIP file
- `description`: Brief description of package contents
- `required`: Whether package is required for basic operation
- `files`: List of files contained in the package

## File Formats

### CSV Files
- UTF-8 encoding
- Comma-separated
- Header row required
- Consistent column naming

### Excel Files (.xlsx)
- Multiple sheets allowed
- First row as headers
- No merged cells in data areas
- Date formats: YYYY-MM-DD

### Pickle Files (.pkl)
- Python 3.8+ compatible
- Pandas DataFrames or NumPy arrays
- Compressed with protocol 4

### Parquet Files
- Apache Parquet format
- Compressed with snappy
- Column-oriented storage

### GeoJSON Files
- Valid GeoJSON format
- EPSG:4326 projection
- Feature properties documented

## Creating a New Package

### 1. Prepare Files
Ensure all files follow the format requirements above.

### 2. Create ZIP Archive
```bash
# Create ZIP with proper structure
zip -r package-name-v1.0.0.zip file1.csv file2.xlsx subfolder/

# Calculate checksum
sha256sum package-name-v1.0.0.zip
```

### 3. Upload to S3
```bash
# Upload to S3 bucket
aws s3 cp package-name-v1.0.0.zip s3://steelo-data/

# Make publicly readable
aws s3api put-object-acl --bucket steelo-data --key package-name-v1.0.0.zip --acl public-read
```

### 4. Update Manifest
Add package entry to `manifest.json`:

```json
{
  "name": "package-name",
  "version": "1.0.0",
  "url": "https://steelo-data.s3.amazonaws.com/package-name-v1.0.0.zip",
  "size_mb": 25.5,
  "checksum": "calculated_sha256_checksum",
  "description": "Description of package contents",
  "required": false,
  "files": ["file1.csv", "file2.xlsx", "subfolder/file3.pkl"]
}
```

### 5. Test Download
```bash
# Test the package downloads correctly
steelo-data-download --package package-name

# Verify integrity
steelo-data-verify
```

## Versioning Strategy

- **Major version**: Breaking changes to file structure or format
- **Minor version**: New files added, backwards compatible
- **Patch version**: Data updates, bug fixes

Examples:
- `1.0.0` → `2.0.0`: Changed CSV column names (breaking)
- `1.0.0` → `1.1.0`: Added new Excel sheet (compatible)
- `1.0.0` → `1.0.1`: Updated data values (patch)

## Best Practices

1. **File Naming**: Use descriptive, lowercase names with underscores
2. **Documentation**: Include README in package if complex
3. **Validation**: Test files with validator before packaging
4. **Compression**: Balance file size with extraction speed
5. **Checksums**: Always verify after upload
6. **Backwards Compatibility**: Maintain old versions when possible

## Package Dependencies

Some packages may depend on others. Document dependencies:

```json
{
  "name": "advanced-geo",
  "dependencies": ["core-data", "geo-data"],
  ...
}
```

Currently, dependency resolution is not automatic - users must download all required packages.