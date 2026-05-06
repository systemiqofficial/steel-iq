# How to Add New Data from Excel Sheets to the Steel Model

This guide explains the complete process of adding new data from Excel sheets to the steel model simulation system. The process involves creating domain objects from Excel data, saving them to JSON repositories, and integrating them into the simulation configuration.

## Overview of the Data Flow

```
Master Excel Sheet → Excel Reader Function → Domain Objects → JSON Repository → SimulationConfig → Simulation
```

The system transforms Excel data through several stages:
1. **Excel parsing**: Functions in `excel_reader.py` read Excel sheets
2. **Domain object creation**: Data is transformed into typed domain objects
3. **JSON serialization**: Objects are saved to JSON repositories
4. **Simulation integration**: JSON paths are configured in `SimulationConfig`

## Step-by-Step Implementation Guide

### 1. Create Your Domain Object (if needed)

First, define a domain object in `src/steelo/domain/models.py` to represent your data:

```python
@dataclass
class EquipmentCost:
    """Domain object for equipment cost data."""
    equipment_id: str
    equipment_name: str
    year: Year
    cost_per_unit: float
    region: str
    
    def __hash__(self):
        return hash((self.equipment_id, self.year, self.region))
```

### 2. Create the Excel Reader Function

Add a reader function in `src/steelo/adapters/dataprocessing/excel_reader.py`:

```python
def read_equipment_costs(excel_path: Path, sheet_name: str = "Equipment Costs") -> list[EquipmentCost]:
    """
    Read equipment costs from Excel sheet and return domain objects.
    
    Args:
        excel_path: Path to the Excel file
        sheet_name: Name of the sheet containing equipment costs
        
    Returns:
        List of EquipmentCost domain objects
    """
    df = pd.read_excel(excel_path, sheet_name=sheet_name)
    
    equipment_costs = []
    for _, row in df.iterrows():
        # Handle missing/invalid data
        if pd.isna(row["Equipment ID"]):
            logger.warning(f"Skipping row with missing Equipment ID")
            continue
            
        try:
            cost = EquipmentCost(
                equipment_id=str(row["Equipment ID"]),
                equipment_name=str(row["Equipment Name"]),
                year=Year(int(row["Year"])),
                cost_per_unit=float(row["Cost ($/unit)"]),
                region=str(row["Region"])
            )
            equipment_costs.append(cost)
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing row: {e}")
            continue
    
    logger.info(f"Read {len(equipment_costs)} equipment cost entries")
    return equipment_costs
```

### 3. Create the JSON Repository Classes

Add repository classes in `src/steelo/adapters/repositories/json_repository.py`:

```python
# Add Pydantic model for JSON serialization
class EquipmentCostInDb(BaseModel):
    equipment_id: str
    equipment_name: str
    year: int
    cost_per_unit: float
    region: str
    
    def to_domain(self) -> EquipmentCost:
        """Convert to domain object."""
        return EquipmentCost(
            equipment_id=self.equipment_id,
            equipment_name=self.equipment_name,
            year=Year(self.year),
            cost_per_unit=self.cost_per_unit,
            region=self.region
        )
    
    @classmethod
    def from_domain(cls, obj: EquipmentCost) -> "EquipmentCostInDb":
        """Create from domain object."""
        return cls(
            equipment_id=obj.equipment_id,
            equipment_name=obj.equipment_name,
            year=obj.year.value,
            cost_per_unit=obj.cost_per_unit,
            region=obj.region
        )

# Add repository class
class EquipmentCostsJsonRepository:
    """Repository for equipment cost data stored in JSON format."""
    
    def __init__(self, path: Path):
        self.path = path
        self._data: list[EquipmentCost] = []
        if self.path.exists():
            self.load()
    
    def add(self, item: EquipmentCost) -> None:
        """Add a single equipment cost entry."""
        self._data.append(item)
        self.save()
    
    def add_list(self, items: list[EquipmentCost]) -> None:
        """Add multiple equipment cost entries."""
        self._data.extend(items)
        self.save()
    
    def list(self) -> list[EquipmentCost]:
        """Return all equipment costs."""
        return self._data.copy()
    
    def get_by_region(self, region: str) -> list[EquipmentCost]:
        """Get equipment costs for a specific region."""
        return [item for item in self._data if item.region == region]
    
    def save(self) -> None:
        """Save data to JSON file."""
        db_items = [EquipmentCostInDb.from_domain(item) for item in self._data]
        with open(self.path, "w") as f:
            json.dump([item.model_dump() for item in db_items], f, indent=2)
    
    def load(self) -> None:
        """Load data from JSON file."""
        with open(self.path, "r") as f:
            data = json.load(f)
        self._data = [EquipmentCostInDb(**item).to_domain() for item in data]
```

### 4. Create the Recreation Function

Add a recreation function in `src/steelo/data/recreation_functions.py`:

```python
def recreate_equipment_costs_data(
    equipment_costs_json_path: Path,
    excel_path: Path,
    sheet_name: str = "Equipment Costs"
) -> EquipmentCostsJsonRepository:
    """
    Recreate equipment costs JSON from master Excel.
    
    Args:
        equipment_costs_json_path: Output path for JSON file
        excel_path: Path to master Excel file
        sheet_name: Name of the sheet containing equipment costs
        
    Returns:
        Repository instance with the loaded data
    """
    console = Console()
    console.print(f"[blue]Reading equipment costs from Excel[/blue]: {excel_path}")
    
    # Read data from Excel
    equipment_costs = read_equipment_costs(excel_path, sheet_name)
    
    # Create repository and save
    repo = EquipmentCostsJsonRepository(equipment_costs_json_path)
    repo.add_list(equipment_costs)
    
    console.print(
        f"[green]Created {equipment_costs_json_path.name} with "
        f"{len(equipment_costs)} equipment cost entries[/green]"
    )
    return repo
```

### 5. Register in Recreation Configuration

Add an entry to `FILE_RECREATION_SPECS` in `src/steelo/data/recreation_config.py`:

```python
FILE_RECREATION_SPECS = {
    # ... existing specs ...
    
    "equipment_costs.json": FileRecreationSpec(
        filename="equipment_costs.json",
        recreate_function="recreate_equipment_costs_data",
        source_type="master-excel",
        master_excel_sheet="Equipment Costs",
        dependencies=[],
        description="Equipment cost data from master Excel",
    ),
}
```

### 6. Update the Data Recreator

In `src/steelo/data/recreate.py`, update the `_recreate_single_file` method:

```python
# In the function_map dictionary (around line 439), add:
function_map["recreate_equipment_costs_data"] = recreate_equipment_costs_data

# In the elif chain for master-excel sources (around line 473), add:
elif spec.recreate_function == "recreate_equipment_costs_data":
    func(
        equipment_costs_json_path=output_path,
        excel_path=master_excel_path,
        sheet_name=spec.master_excel_sheet,
    )
```

### 7. Add Path to SimulationConfig (If Needed)

**Note:** In the current architecture, SimulationConfig doesn't store individual JSON paths for each data type. Instead, it uses a `data_dir` that points to the directory containing all fixtures. The JsonRepository is created with paths to all JSON files in the fixtures directory.

If your data needs special handling or paths, you may need to:
1. Add it to the JsonRepository initialization in `SimulationConfig._create_repository()` method
2. Pass the path when creating the JsonRepository instance

For most cases, no changes to SimulationConfig are needed as long as your JSON file is in the fixtures directory.

### 8. Update JsonRepository (if using centralized repository)

If you're using the centralized `JsonRepository` class in `src/steelo/adapters/repositories/json_repository.py`, add your repository:

```python
class JsonRepository:
    # Add as a class attribute (around line 2563)
    equipment_costs: EquipmentCostsJsonRepository
    
    def __init__(
        self,
        # ... existing parameters ...
        equipment_costs_path: Optional[Path] = None,  # Add parameter
    ):
        # ... existing code ...
        
        # Initialize equipment costs repository (in __init__ method)
        if equipment_costs_path:
            self.equipment_costs = EquipmentCostsJsonRepository(equipment_costs_path)
        else:
            # Create empty repository if path not provided
            import tempfile
            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            temp_file.write("[]")  # Empty JSON array
            temp_file.close()
            self.equipment_costs = EquipmentCostsJsonRepository(Path(temp_file.name))
```

Then update the JsonRepository creation in `SimulationConfig._create_repository()` (around line 362):

```python
json_repo = JsonRepository(
    # ... existing paths ...
    equipment_costs_path=fixtures_dir / "equipment_costs.json",
)
```

### 9. Integrate into Simulation

The data is automatically loaded when the simulation starts through the JsonRepository. To use your data in the simulation:

```python
# In SimulationRunner.run() or wherever you need the data:
def run(self):
    # ... existing code ...
    
    # Access your data through the repository
    # The JsonRepository is available through config._json_repository
    equipment_costs = self.config._json_repository.equipment_costs.list()
    
    # Use the data in your simulation logic, for example:
    # Option 1: Pass to Environment
    self.bus.env.initiate_equipment_costs(equipment_costs)
    
    # Option 2: Add to in-memory repository if needed
    for cost in equipment_costs:
        self.bus.uow.equipment_costs.add(cost)
```

## Excel Sheet Format Example

Your Excel sheet should have a clear structure. For the equipment costs example:

```
Equipment Costs
| Equipment ID | Equipment Name | Year | Cost ($/unit) | Region |
|--------------|----------------|------|---------------|---------|
| EQ001        | Blast Furnace  | 2025 | 1000000      | USA     |
| EQ002        | EAF            | 2025 | 500000       | EUR     |
| EQ003        | DRI Unit       | 2026 | 750000       | CHN     |
```

## Best Practices

### 1. Data Validation
- Always validate data during reading
- Handle missing or invalid values gracefully
- Log warnings for skipped rows

```python
if pd.isna(row["Critical Field"]):
    logger.warning(f"Skipping row with missing critical field")
    continue
```

### 2. Unit Conversions
- Document expected units in the Excel sheet
- Convert units consistently

```python
# Convert from kg to tonnes
if "kg" in row["Unit"]:
    value = value / 1000
```

### 3. Error Handling
- Use try-except blocks for parsing
- Provide meaningful error messages
- Continue processing other rows on error

```python
try:
    year = Year(int(row["Year"]))
except ValueError:
    logger.warning(f"Invalid year: {row['Year']}")
    continue
```

### 4. Testing
Create tests for your reader function:

```python
def test_read_equipment_costs():
    """Test reading equipment costs from Excel."""
    test_excel = Path("tests/fixtures/test_equipment_costs.xlsx")
    costs = read_equipment_costs(test_excel)
    
    assert len(costs) > 0
    assert all(isinstance(c, EquipmentCost) for c in costs)
    assert costs[0].equipment_id == "EQ001"
```

## How Data Flows in Both CLI and Django

### CLI Usage
When running `steelo-data-prepare`:
1. `DataPreparationService` orchestrates the process
2. `DataRecreator.recreate_from_package()` is called
3. Your recreation function is invoked via the function map
4. JSON file is created in the output directory

### Django Integration
When uploading through the web UI:
1. User uploads master Excel file
2. Django view calls `DataPreparationService`
3. Same recreation process runs in background
4. JSON files are created in the repository directory

## Troubleshooting

### Common Issues

1. **Sheet not found**: Ensure sheet name in `FILE_RECREATION_SPECS` matches exactly
2. **Function not found**: Make sure to add your function to the `function_map`
3. **Import errors**: Add necessary imports for your domain objects
4. **Path issues**: Use `ensure_path()` to handle string/Path conversions

### Debugging Tips

1. Enable verbose logging via CLI:
```bash
steelo-data-prepare --verbose
```

Or run with DEBUG level:
```bash
run_simulation --log-level DEBUG ...
```

2. Test your reader function independently:
```python
from steelo.adapters.dataprocessing.excel_reader import read_equipment_costs
costs = read_equipment_costs(Path("master_input.xlsx"))
print(f"Read {len(costs)} entries")
```

3. Verify JSON output:
```python
import json
with open("fixtures/equipment_costs.json") as f:
    data = json.load(f)
    print(f"JSON contains {len(data)} entries")
```

## Summary

Adding new Excel data involves:
1. Creating domain objects to represent your data (in `src/steelo/domain/models.py`)
2. Writing an Excel reader function to parse the sheet (in `src/steelo/adapters/dataprocessing/excel_reader.py`)
3. Creating JSON repository classes for persistence (in `src/steelo/adapters/repositories/json_repository.py`)
4. Adding a recreation function to orchestrate the process (in `src/steelo/data/recreation_functions.py`)
5. Registering everything in the configuration (in `src/steelo/data/recreation_config.py`)
6. Updating the data recreator to handle your function (in `src/steelo/data/recreate.py`)
7. Optionally updating the centralized JsonRepository class
8. Integrating with the simulation to use your data

The system is designed to be modular and extensible, following consistent patterns throughout. By following this guide, you can add any new Excel data source to the steel model simulation system.

## Important Notes

- The documentation accurately reflects the current implementation as of the last verification
- All file paths and line numbers mentioned are approximate and may vary slightly
- The recreation functions are now centralized in `src/steelo/data/recreation_functions.py` as per the architecture guidelines
- SimulationConfig uses a fixtures directory approach rather than individual path fields for each data type