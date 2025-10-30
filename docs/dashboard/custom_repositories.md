# Custom Repositories in Steel Model UI

## Overview

Steel Model allows users to upload and manage custom repositories for simulations. Currently, the system supports custom Plants repositories with plans to expand to other repository types in the future.

## Repository Types

- **Plants Repository**: Contains information about steel plants including their location, furnace groups, capacities, etc.

## Creating a Repository

1. Navigate to the "Repositories" section in the main navigation.
2. Click the "Create Repository" button.
3. Fill in the form:
   - **Name**: A descriptive name for the repository
   - **Description**: Optional details about the repository's contents or purpose
   - **Repository Type**: Select "Plants" for plants data
   - **JSON File**: Upload your repository JSON file (must be a valid JSON file)
4. Click "Save Repository"

## File Format Requirements

### Plants Repository Format

The plants repository must be a valid JSON file following the structure used in the default plants.json file. The file should contain an array of plant objects, each with the following structure:

```json
{
  "plant_id": "unique_id",
  "location": {
    "iso3": "GBR",
    "country": "United Kingdom", 
    "region": "Europe",
    "lat": 51.5074,
    "lon": -0.1278
  },
  "furnace_groups": [
    {
      "furnace_group_id": "fg_unique_id",
      "capacity": 500000,
      "status": "operating",
      "lifetime": {
        "current": 2025,
        "time_frame": {
          "start": 2020,
          "end": 2045
        }
      },
      "last_renovation_date": "2015-01-01",
      "technology": {
        "name": "BF-BOF",
        "product": "Steel",
        "technology_readiness_level": 9,
        "process_emissions": 1.6,
        "emissions_factor": 1.8,
        "energy_consumption": 15
      },
      "historical_production": {
        "2020": 450000,
        "2021": 480000,
        "2022": 490000
      },
      "utilization_rate": 0.9
    }
  ],
  "power_source": "grid",
  "soe_status": "private",
  "parent_gem_id": "parent_id",
  "workforce_size": 500,
  "certified": true,
  "category_steel_product": ["flat", "long"]
}
```

## Using Custom Repositories in Simulations

You can use custom repositories when creating a new simulation or modify an existing simulation to use a custom repository:

### When Creating a New Simulation:

1. Go to "Create Simulation"
2. Fill in the simulation parameters (start year, end year, etc.)
3. Select your custom repository from the "Plants Repository" dropdown
4. Click "Create Simulation"

### For Existing Simulations:

1. Navigate to the detail page of the simulation
2. Under "Configuration", click the "Actions" dropdown
3. Select "Add Repository"
4. Choose an existing repository or upload a new one
5. Save your changes

## Notes and Limitations

- Custom repositories must follow the exact format expected by the simulation engine
- The repository file size is limited to 10MB
- Only JSON files are accepted
- A simulation can only use one plants repository at a time
- You cannot change the repository once a simulation has started running

## Future Plans

In future versions, we plan to add support for:
- Validation of repository JSON against a schema
- Visual editor for repositories
- Support for other repository types (demand centers, costs, etc.)
- Merging and comparing repositories