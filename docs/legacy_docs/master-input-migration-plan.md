# Master Input File Migration Plan

## Overview

This document outlines the strategy for migrating from the current core-data zip archive to a unified master input Excel file. The migration will be done incrementally to ensure stability and allow for testing at each stage.

## Current Architecture

### Data Flow
1. **S3 Storage**: Raw data packages stored as zip archives
   - core-data-v1.0.3.zip (2.7 MB)
   - geo-data-v1.0.6.zip (91.0 MB)

2. **Data Transformation**:
   - CLI: Downloads to `$STEELO_HOME/data_cache/` → processes to `project_root/data/`
   - Django: `DataPackage` models → `DataPreparation` models → Django media directory

3. **SimulationConfig**: Central configuration object with file paths passed to SimulationRunner

## Master Input File Structure

The master input Excel file (`2025_06_12_Master input file_WIP.xlsx`) contains the following sheets:

### Core Data Sheets
- **Iron and steel plants** → Replaces: `steel_plants_input_data_2025-03.csv`
- **Iron ore mines** → Replaces: `mining_data.xlsx`
- **Bill of Materials** → Replaces: `BOM_ghg_system_boundary_v6.xlsx`
- **Techno-economic details** → Replaces: `technology_lcop.csv`
- **Allowed tech switches** → Replaces: `tech_switches_allowed.csv`
- **Demand and scrap availability** → Replaces: `2025_05_27 Demand outputs for trade module.xlsx`
- **Input costs** → Replaces: `input_costs_for_python_model.csv`, `regional_input_costs.json`
- **Carbon cost** → Replaces: `carbon_costs_iso3_year.xlsx`
- **Tariffs** → Replaces: `Data_collection_ultimate_steel.xlsx` (tariff sheet)
- **Steel/Iron production by tech** → Replaces: `steel_production_2019to2022.csv`, `iron_production_2019to2022.csv`

### Additional Data Sheets (New)
- **Biomass availability**
- **CO2 storage**
- **Power grid data**
- **Cost of capital**
- **Country mapping**
- **Distances**
- **Trade bloc definitions**
- **Subsidies**
- **Legal Process connectors**
- **Power grid emissivity**

## Data Mapping

### SimulationConfig Fields to Master Input Mapping

| SimulationConfig Field | Current Source File | Master Input Sheet | Priority |
|------------------------|---------------------|-------------------|----------|
| plants_json_path | steel_plants_input_data_2025-03.csv | Iron and steel plants | HIGH |
| technology_lcop_csv | technology_lcop.csv | Techno-economic details | HIGH |
| demand_center_xlsx | 2025_05_27 Demand outputs for trade module.xlsx | Demand and scrap availability | HIGH |
| tech_switches_csv_path | tech_switches_allowed.csv | Allowed tech switches | MEDIUM |
| mine_data_excel | mining_data.xlsx | Iron ore mines | MEDIUM |
| carbon_costs_xlsx | carbon_costs_iso3_year.xlsx | Carbon cost | MEDIUM |
| tariff_excel | Data_collection_ultimate_steel.xlsx | Tariffs | MEDIUM |
| input_costs_csv_raw | input_costs_for_python_model.csv | Input costs | HIGH |
| new_business_cases_excel | BOM_ghg_system_boundary_v6.xlsx | Bill of Materials | HIGH |
| hist_production_csv | historical_production_data.csv | Steel/Iron production sheets | LOW |
| gravity_distances_csv | gravity_distances_dict.pkl | Distances | LOW |

### Data Not in Master Input (Keep from core-data)
- `countries.csv` → Could be derived from Country mapping sheet
- `cost_of_x.json` → May need separate handling
- `geolocator_raster.csv` → Geo-specific, keep separate
- `Regional_Energy_prices.xlsx` → Partially covered by Power grid data

### New Data in Master Input (Not in current system)
- Biomass availability
- CO2 storage capacity and costs
- Detailed power grid emissions
- Cost of capital by region
- Trade bloc definitions
- Subsidies information

## Important: Coordination and Communication

### Lesson Learned: The July 2025 Parallel Development Incident

During the migration effort in July 2025, we experienced an unintentional code overwrite that serves as an important lesson for team coordination:

#### What Happened
- **July 17, 2025**: Ioana implemented `SecondaryFeedstockConstraint` and `TransportData` classes for handling biomass constraints and transport emissions
- **July 21, 2025**: Jochen, unaware of Ioana's implementation, completely rewrote these classes with more comprehensive implementations
- **Result**: Ioana's original work was overwritten, causing frustration and lost effort

#### The Technical Details
Ioana's original implementation (simple, functional):
```python
class SecondaryFeedstockConstraint:
    def __init__(self, secondary_feedstock_name: str, region_iso3s: list[str], maximum_constraint_per_year: dict[Year, float]):
        self.secondary_feedstock_name = secondary_feedstock_name
        self.region_iso3s = region_iso3s
        self.maximum_constraint_per_year = maximum_constraint_per_year
```

Was replaced within hours by a 100+ line implementation with validation, documentation, and helper methods.

#### Best Practices to Prevent This

1. **Check Recent Commits**: Before implementing new features, always check recent commits in the area you're working on:
   ```bash
   git log --oneline --since="3 days ago" --grep="constraint\|transport\|biomass" -i
   ```

2. **Communicate in Slack/Teams**: Post a quick message when starting work on a new data type:
   > "Working on biomass constraints and transport emissions reading from master Excel"

3. **Check for Existing Implementations**: Search the codebase before creating new classes:
   ```bash
   grep -r "class.*Transport" src/
   grep -r "SecondaryFeedstock" src/
   ```

4. **Incremental Enhancement**: If you find a simple implementation, enhance it rather than rewriting:
   - Add validation in a separate commit
   - Add documentation in another commit
   - This preserves attribution and makes changes trackable

5. **Daily Standups**: Mention what data types you're migrating in standups

6. **Claim Your Territory**: Update this document's "In Progress" section when starting work

## Migration Strategy

### Phase 1: Infrastructure & Core Migration ✅ COMPLETED

1. **Updated DataPreparation model**:
   ```python
   class DataPreparation(models.Model):
       # Existing fields...
       master_excel_file = models.FileField(
           upload_to="data_preparations/master_excel/",
           blank=True,
           null=True,
           help_text="Optional master Excel input file to override default data files"
       )
       master_excel_validation_report = models.JSONField(
           default=dict,
           blank=True,
           help_text="Validation report from master Excel file"
       )
   ```

2. **DataPreparationService Integration**:
   - Added `_process_master_excel()` method that extracts data during preparation
   - Master Excel is processed alongside core and geo data packages
   - Extracted files are saved to fixtures directory

3. **Excel Reader Module Refactoring** ✅ (January 2025):
   - Refactored excel_reader.py into domain-specific modules
   - Created separate modules for: biomass, constraints, environmental, feedstocks, financial, hydrogen, legal_process, mappings, plants, prices, suppliers, trade
   - Maintains backward compatibility through __init__.py re-exports

4. **Create MasterExcelReader adapter with built-in validation**:
   ```python
   # src/steelo/adapters/dataprocessing/master_excel_reader.py
   class MasterExcelReader:
       def __init__(self, excel_path: Path):
           self.excel_path = excel_path
           self._data = {}
           self.validator = MasterExcelValidator()
       
       def read_and_validate_plants(self) -> Tuple[pd.DataFrame, List[ValidationError]]:
           """Read and validate plant data from master input"""
           df = pd.read_excel(self.excel_path, sheet_name="Iron and steel plants")
           errors = self.validator.validate_plants(df)
           return df, errors
       
       def to_repository_format(self) -> Dict[str, Any]:
           """Convert all data to repository JSON format"""
           pass
   ```

5. **Shared validation for both CLI and Django**:
   ```python
   # src/steelo/adapters/dataprocessing/master_excel_validator.py
   class MasterExcelValidator:
       """Shared validator used by both steelo-data-prepare and Django forms"""
       
       def validate_plants(self, df: pd.DataFrame) -> List[ValidationError]:
           errors = []
           # Check required columns
           required_cols = ['plant_id', 'country', 'capacity', 'technology']
           missing = set(required_cols) - set(df.columns)
           if missing:
               errors.append(ValidationError("Missing columns", missing))
           return errors
   ```

6. **Django form integration** ✅ COMPLETED:
   ```python
   # src/steeloweb/forms.py
   class DataPreparationForm(forms.ModelForm):
       class Meta:
           model = DataPreparation
           fields = ["name", "core_data_package", "geo_data_package", "master_excel_file"]
       
       def clean_master_excel_file(self):
           file = self.cleaned_data['master_excel_file']
           if file:
               # Validate using MasterExcelValidator
               validator = MasterExcelValidator()
               report = validator.validate_file(tmp_path)
               
               if report.has_errors():
                   raise forms.ValidationError(
                       f"Master Excel validation failed with {len(report.errors)} errors"
                   )
               
               # Store validation report
               self.instance.master_excel_validation_report = {
                   'errors': [str(e) for e in report.errors],
                   'warnings': [str(w) for w in report.warnings],
                   'summary': {'error_count': len(report.errors), ...}
               }
   ```

7. **Django Admin Integration** ✅ COMPLETED:
   - DataPreparationAdmin uses DataPreparationForm
   - Shows validation report with formatted HTML display
   - "Has Master Excel" indicator in list view

8. **ModelRun Integration** ✅ COMPLETED:
   - Removed hardcoded USE_MASTER_EXCEL processing
   - Uses extracted files from DataPreparation fixtures directory
   - Currently only uses tech_switches_allowed.csv

### Phase 2: Complete Migration & Testing (Days 6-10)

1. **Update steelo-data-prepare**:
   ```bash
   # New command options
   steelo-data-prepare --use-master-input
   steelo-data-prepare --validate-only  # Just run validation
   ```

2. **Implement all data extractors in parallel**:
   - High Priority (Days 6-7): Plants, BOM, Input costs, Demand
   - Medium Priority (Day 8): Tech details, Carbon costs, Tariffs  
   - Low Priority (Day 9): Historical data, Mining, Distances
   - New Features (Day 10): Biomass, CO2 storage, Power grid

### Phase 3: Polish & Deploy (Days 11-15)

1. **Comprehensive testing**:
   - Automated comparison tests between core-data and master input
   - End-to-end simulation runs with both sources
   - Performance benchmarking

2. **User interface polish**:
   - Progress indicators for validation
   - Clear error messages with row/column references
   - Download validation report as CSV/Excel
   - "Download Master Input Template" button in UI
   - Template versioning with automatic updates

3. **Documentation & training**:
   - Update user guides
   - Create example master input files
   - Record demo video

## Implementation Details

### SimulationConfig Updates

```python
@dataclass
class SimulationConfig:
    # Existing fields...
    
    # Master input support
    use_master_input: bool = False
    master_input_path: Path = Auto
    
    def get_plants_data_path(self) -> Path:
        """Return appropriate plants data path based on configuration"""
        if self.use_master_input and self.master_input_path:
            return self.master_input_path  # Reader will extract correct sheet
        return self.plants_json_path
```

### Data Extraction Pipeline

```python
def prepare_data_from_master(master_path: Path, output_dir: Path) -> Dict[str, Path]:
    """Extract and prepare all data from master input file"""
    reader = MasterExcelReader(master_path)
    prepared_files = {}
    
    # Extract each data type
    plants_df = reader.read_plants()
    plants_json = convert_plants_to_repository_format(plants_df)
    plants_path = output_dir / "plants.json"
    plants_path.write_text(json.dumps(plants_json, indent=2))
    prepared_files['plants'] = plants_path
    
    # ... repeat for each data type
    
    return prepared_files
```

## Testing Strategy

### Validation Approach

The validation system is shared between CLI and Django to ensure consistency:

1. **CLI Validation (`steelo-data-prepare`)**:
   ```python
   def prepare_data(args):
       if args.use_master_input:
           reader = MasterExcelReader(args.master_path)
           validator = MasterExcelValidator()
           
           # Validate all sheets
           validation_report = validator.validate_all(reader.excel_path)
           
           if args.validate_only:
               print_validation_report(validation_report)
               return
           
           if validation_report.has_errors():
               logger.error("Validation failed")
               return False
               
           # Proceed with data preparation
           prepared_data = reader.to_repository_format()
   ```

2. **Django Form Validation**:
   - Reuses same `MasterExcelValidator` class
   - Provides immediate feedback on upload
   - Stores validation results in database for async processing

### Test Coverage

1. **Unit Tests**:
   - Each validation method in `MasterExcelValidator`
   - Data transformation logic in `MasterExcelReader`
   - Edge cases (empty sheets, missing columns, invalid types)

2. **Integration Tests**:
   - Full pipeline: Excel → Validation → JSON repositories
   - Comparison with core-data outputs
   - SimulationRunner with both data sources

3. **Performance Tests**:
   - Large Excel files (10k+ plants)
   - Validation speed benchmarks
   - Memory usage profiling

## Rollback Plan

1. **Feature flag control**: Disable USE_MASTER_EXCEL
2. **Database rollback**: Remove master_data_package references
3. **Configuration reset**: Revert SimulationConfig changes
4. **Cache cleanup**: Clear any master input cached data

## Success Metrics

1. **Data Parity**: 100% of core-data can be sourced from master input
2. **Performance**: Data preparation time within 10% of current
3. **Validation Coverage**: All data validated before use
4. **User Experience**: Clear error messages for invalid uploads
5. **Backward Compatibility**: Existing simulations continue to work

## Timeline (3 Weeks Total)

### Week 1: Core Development
- Days 1-2: Infrastructure setup (models, validators, reader)
- Days 3-5: Implement high-priority data migrations

### Week 2: Complete Migration
- Days 6-8: Medium and low priority migrations
- Days 9-10: New features (biomass, CO2, etc.)

### Week 3: Testing & Deployment
- Days 11-12: Comprehensive testing
- Days 13-14: UI polish and documentation
- Day 15: Production rollout

## Data Ownership & Dependencies

### Sheet Dependencies & Ownership Matrix

| Sheet Name | Module Dependencies | Domain | Infrastructure | Time Estimate |
|------------|-------------------|--------------|---------------------|---------------|
| **Iron and steel plants** | `PlantRepository`, `GeospatialModel`, `PlantAgentsModel` | Ioana | Jochen | I: 1.5h, J: 3.5h |
| **Iron ore mines** | `SupplierRepository`, `AllocationModel` | Liz | Jochen | L: 0.75h, J: 1.75h |
| **Bill of Materials** | `Environment.dynamic_feedstocks`, `TM_PAM_connector` | Ioana | Jochen | I: 2.5h, J: 2.5h |
| **Techno-economic details** | `Technology`, `Environment.capex` | Ioana | Jochen | I: 1.5h, J: 1.75h |
| **Allowed tech switches** | `Plant.evaluate_furnace_group_strategy` | Ioana | Jochen | I: 0.75h, J: 0.75h |
| **Demand and scrap availability** | `DemandCenterRepository`, `Environment.calculate_demand` | Liz | Jochen | L: 1.5h, J: 2.5h |
| **Input costs** | `Environment.input_costs`, cost calculations | Liz | Jochen | L: 0.75h, J: 1.75h |
| **Carbon cost** | `Environment.carbon_costs`, emissions calculations | Ioana | Jochen | I: 0.75h, J: 1.75h |
| **Tariffs** | `TariffRepository`, `AllocationModel` | Liz | Jochen | L: 1.5h, J: 1.75h |
| **Steel/Iron production by tech** | Historical data validation | Liz | Jochen | L: 0.75h, J: 0.75h |
| **Biomass availability** | Future: `SupplierRepository` extension | Ioana | Jochen | I: 1.5h, J: 2.5h |
| **CO2 storage** | Future: New CO2 module | Ioana | Jochen | I: 2.5h, J: 3.5h |
| **Power grid data** | `GeospatialModel`, energy costs | Ioana | Jochen | I: 1.5h, J: 2.5h |
| **Cost of capital** | `Environment.cost_of_capital` | Liz | Jochen | L: 0.75h, J: 0.75h |
| **Country mapping** | All modules using ISO3 codes | Liz | Jochen | L: 0.5h, J: 0.75h |
| **Distances** | `AllocationModel`, transport costs | Liz | Jochen | L: 0.75h, J: 1.75h |
| **Trade bloc definitions** | `TariffRepository`, trade rules | Liz | Jochen | L: 1.5h, J: 1.75h |
| **Subsidies** | Future: Cost adjustments | Liz | Jochen | L: 1.5h, J: 2.5h |
| **Legal Process connectors** | Trade module process mapping | Liz | Jochen | L: 0.75h, J: 0.75h |
| **Power grid emissivity** | Emissions calculations | Ioana | Jochen | I: 0.75h, J: 1.75h |

**Time Estimate Key**: I = Ioana, L = Liz, J = Jochen

### Time Breakdown by Phase

#### Phase 1: Design & Specification (Domain Owners)
| Task | Ioana | Liz | Total |
|------|-------|-----|-------|
| High Priority Sheets | 6h | 3h | 9h |
| Medium Priority Sheets | 3h | 4h | 7h |
| Low Priority Sheets | - | 2.75h | 2.75h |
| New Features | 6.5h | 2.5h | 9h |
| **Total Phase 1** | **15.5h** | **12.25h** | **27.75h** |

#### Phase 2: Implementation (Infrastructure - Jochen)
| Task | Hours |
|------|-------|
| Core Infrastructure (validator, reader, models) | 7h |
| High Priority Sheets (Plants, BOM, Costs, Demand) | 10h |
| Medium Priority Sheets | 8.5h |
| Low Priority Sheets | 5h |
| New Features | 13.5h |
| Integration & Testing | 6h |
| **Total Phase 2** | **50h** |

#### Phase 3: Review & Testing (All)
| Task | Ioana | Liz | Jochen | Total |
|------|-------|-----|--------|-------|
| Data validation testing | 3.5h | 3h | 4h | 10.5h |
| Integration testing | 2h | 1.75h | 4h | 7.75h |
| Documentation review | 1h | 1h | 2h | 4h |
| **Total Phase 3** | **6.5h** | **5.75h** | **10h** | **22.25h** |

### Total Time Investment
- **Ioana**: 22 hours (2.75 days)
- **Liz**: 18 hours (2.25 days)
- **Jochen**: 60 hours (7.5 days)
- **Total Project**: 100 hours

### Responsibility Guidelines

**Domain Owners (Ioana/Liz)**:
- Specify how the data should be passed to their modules
- Define required data fields and validation rules
- Specify business logic and constraints
- Review and approve data transformations
- Ensure data quality and consistency

**Infrastructure Owner (Jochen)**:
- Design data extraction and transformation pipeline
- Implement validation framework
- Ensure performance and scalability
- Handle file I/O, caching, and error handling
- Maintain backward compatibility

**Data Format Design Process**:
1. Domain owner specifies requirements
2. Infrastructure owner proposes technical format
3. Joint review and iteration
4. Implementation by infrastructure owner
5. Testing and validation by domain owner

## Current Implementation Status

### Completed Architecture ✅
1. **Data Model**: Added master_excel_file and validation report to DataPreparation
2. **Validation**: Form-based validation using MasterExcelValidator before saving
3. **Processing**: DataPreparationService extracts files during data preparation
4. **Integration**: ModelRun uses extracted files from fixtures directory
5. **Admin UI**: Shows validation report and upload status
6. **Recreation Functions**: Separated from CLI dependencies - all recreation functions now in `recreation_functions.py`
7. **Recreation Configuration**: Modular configuration system in `recreation_config.py` with FileRecreationSpec definitions

### Core Data Migration Status (as of 2025-08-06)

#### Files Successfully Migrated ✅
| Core-Data File | Master Excel Sheet | Status | Implementation Date | Owner |
|---|---|---|---|---|
| tech_switches_allowed.csv | Allowed tech switches | ✅ Implemented | January 2025 | Jochen |
| railway_costs.json | Railway cost | ✅ Implemented | January 2025 | Jochen |
| carbon_costs_iso3_year.xlsx | Carbon costs | ✅ Implemented | January 2025 | Jochen |
| countries.csv | Country mapping | ✅ Implemented | January 2025 | Jochen |
| transport_emissions.json | Transport emissions | ✅ Implemented | July 2025 | Jochen/Ioana |
| biomass_availability.json | Biomass availability | ✅ Implemented | July 2025 | Jochen/Ioana |

#### Files with Master Excel Mapping (Ready for Implementation) 📋
| Core-Data File | Master Excel Sheet | JSON Output | Priority | Est. Hours | Notes |
|---|---|---|---|---|---|
| steel_plants_input_data_2025-03.csv | Iron and steel plants | plants.json | HIGH | 3-4h | `read_plants()` method exists in MasterExcelReader |
| mining_data.xlsx | Iron ore mines | suppliers.json | MEDIUM | 2-3h | Recreation function exists |
| BOM_ghg_system_boundary_v6.xlsx | Bill of Materials | primary_feedstocks.json | HIGH | 3-4h | `read_bom()` method exists in MasterExcelReader |
| technology_lcop.csv | Techno-economic details | - | HIGH | 3-4h | |
| 2025_05_27 Demand outputs for trade module.xlsx | Demand and scrap availability | demand_centers.json | HIGH | 3-4h | Recreation function exists |
| input_costs_for_python_model.csv | Input costs | input_costs.json | HIGH | 2-3h | Recreation function exists |
| Data_collection_ultimate_steel.xlsx | Tariffs | tariffs.json | MEDIUM | 2-3h | Recreation function exists |
| regional_input_costs.json | (Derived from Input costs) | regional_input_costs.json | LOW | Already done | Derived file |
| gravity_distances_dict.pkl | Distances | - | LOW | 2-3h | |

#### Files Without Direct Master Excel Replacement ❌
| Core-Data File | Current Usage | Migration Strategy | Est. Hours |
|---|---|---|---|
| historical_production_data.csv | Historical validation | Merge with Steel/Iron production sheets | 4-6h |
| iron_production_2019to2022.csv | Production history | Use Steel/Iron production by tech sheet | (included above) |
| steel_production_2019to2022.csv | Production history | Use Steel/Iron production by tech sheet | (included above) |
| cost_of_x.json | Various cost parameters | Keep separate or split across sheets | 6-8h |
| Regional_Energy_prices.xlsx | Energy prices by region | Use Power grid data sheet (partial) | 3-4h |
| geolocator_raster.csv | Location coordinates | **To be removed** - generated on the fly | 0h |

### Completed Implementations Detail

#### Transport Emissions (July 2025) ✅
- **Reader**: `read_transport_emissions()` in `environmental.py`
- **Repository**: `TransportEmissionsJsonRepository`
- **Domain Model**: `TransportEmission` dataclass
- **Recreation**: `recreate_transport_emissions_data()`
- **Sheet**: "Transport emissions"
- **Usage**: Maps (reporter_iso, partner_iso, commodity) → emission factor

#### Biomass Availability (July 2025) ✅
- **Reader**: `read_biomass_availability()` in `biomass.py`
- **Repository**: `BiomassAvailabilityJsonRepository`
- **Domain Model**: `BiomassAvailability` dataclass
- **Constraint Model**: `SecondaryFeedstockConstraint`
- **Recreation**: `recreate_biomass_availability_data()`
- **Sheet**: "Biomass availability"
- **Usage**: Regional biomass constraints by year, converted to SecondaryFeedstockConstraint

### Detailed Implementation Tasks

#### 1. technology_lcop.csv → Techno-economic details (HIGH PRIORITY)
**Todo:**
- [ ] Add `read_technology_lcop()` method to MasterExcelReader
- [ ] Map columns: technology, year, capex, opex, efficiency
- [ ] Validate technology names match known technologies
- [ ] Generate technology_lcop.csv output format
- [ ] Add to recreation_config.py FILE_RECREATION_SPECS
- [ ] Write unit tests

#### 2. steel_plants_input_data_2025-03.csv → Iron and steel plants (HIGH PRIORITY)
**Status: Partially Complete**
- [x] `read_plants()` method exists in MasterExcelReader
- [x] recreate_plants_data() function exists
- [ ] Full integration with SimulationConfig
- [ ] Validate against existing plant data format
- [ ] Integration test with PlantRepository

#### 3. BOM_ghg_system_boundary_v6.xlsx → Bill of Materials (HIGH PRIORITY)
**Status: Partially Complete**
- [x] `read_bom()` method exists in MasterExcelReader
- [x] recreate_primary_feedstock_data() function exists
- [ ] Full integration with SimulationConfig
- [ ] Validate against existing BOM format

#### 4. 2025_05_27 Demand outputs for trade module.xlsx → Demand and scrap availability (HIGH PRIORITY)
**Todo:**
- [ ] Update recreate_demand_center_data() implementation
- [ ] Extract demand by year columns
- [ ] Extract scrap availability data
- [ ] Generate demand_centers.json format
- [ ] Handle gravity distances dependency
- [ ] Test with DemandCenterRepository

#### 5. input_costs_for_python_model.csv → Input costs (HIGH PRIORITY)
**Todo:**
- [ ] Implement recreate_input_costs_data()
- [ ] Map regional cost variations
- [ ] Handle year-based cost projections
- [ ] Generate input_costs.json
- [ ] Ensure compatibility with Environment.input_costs
- [ ] Validate cost categories

#### 6. Transportation costs worksheet → transportation_cost_per_km (NEW - HIGH PRIORITY)
**Todo:**
- [ ] Add `read_transportation_costs()` method to MasterExcelReader
- [ ] Parse commodity-specific costs per km
- [ ] Replace hardcoded values in SimulationConfig.transportation_cost_per_km
- [ ] Create TransportationCostsJsonRepository
- [ ] Add recreation function
- [ ] Validate commodity names match simulation commodities
**Note**: Worksheet exists in master Excel but has no reader implementation yet

#### 7. gravity_distances_dict.pkl → Distances (LOW PRIORITY)
**Todo:**
- [ ] Add read_distances() method to MasterExcelReader
- [ ] Convert from pickle to JSON format
- [ ] Handle country-to-country distance matrix
- [ ] Validate distance calculations
- [ ] Consider caching for performance
- [ ] Update references from .pkl to .json

#### 7. Production History Files (MEDIUM PRIORITY)
**Todo:**
- [ ] Analyze Steel/Iron production by tech sheet structure
- [ ] Merge logic for 3 separate production files
- [ ] Create unified production history extractor
- [ ] Handle technology-specific production data
- [ ] Validate against historical years (2019-2022)
- [ ] Test with production validation logic

#### 8. cost_of_x.json Analysis (LOW PRIORITY)
**Todo:**
- [ ] Audit all usages of cost_of_x.json in codebase
- [ ] Identify which parameters are still needed
- [ ] Determine if can be split across existing sheets
- [ ] Document any parameters that need new sheets
- [ ] Create migration plan or keep as supplementary file
- [ ] Update documentation

#### 9. Regional_Energy_prices.xlsx → Power grid data (MEDIUM PRIORITY)
**Todo:**
- [ ] Compare existing sheets with Power grid data coverage
- [ ] Identify missing fields
- [ ] Extend Power grid data sheet if needed
- [ ] Update excel_reader.py to use new source
- [ ] Test grid price calculations
- [ ] Validate regional mappings

## Current Migration Step: Tech Switches ✅ COMPLETED

### Overview
We successfully migrated `tech_switches_csv_path` as the first data type.

### Implementation Progress

#### 1. MasterExcelReader Class ✅
Created `src/steelo/adapters/dataprocessing/master_excel_reader.py` with:
- Context manager support for proper Excel file handling
- `read_tech_switches()` method that:
  - Reads "Allowed tech switches" sheet
  - Validates it's a square matrix
  - Converts to CSV format
  - Returns `ExtractionResult` with file path or errors

```python
# Usage example
from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader

with MasterExcelReader(master_excel_path) as reader:
    result = reader.read_tech_switches()
    if result.success:
        config['tech_switches_csv_path'] = result.file_path
```

#### 2. Integration with SimulationConfig ✅
Updated `steeloweb/models.py` to use MasterExcelReader when `USE_MASTER_EXCEL` flag is True.

```python
# Implemented in ModelRun.run()
if USE_MASTER_EXCEL and self.data_preparation and self.data_preparation.is_ready():
    master_excel_path = fixtures_dir / "2025_06_12_Master input file_WIP.xlsx"
    
    if master_excel_path.exists():
        from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader
        
        with MasterExcelReader(master_excel_path) as reader:
            extracted_paths = reader.get_output_paths()
            
            # Override config with extracted paths
            for field_name, file_path in extracted_paths.items():
                if field_name in config_data:
                    config_data[field_name] = str(file_path)
```

The integration includes:
- Automatic detection of master Excel file in prepared data
- Extraction of available data fields (currently tech_switches)
- Override of config paths with extracted data
- Comprehensive error logging

#### 3. Testing Strategy ✅
Created comprehensive test suite in `tests/unit/test_master_excel_reader.py`:
- Unit tests with mock Excel data in temp directory
- Tests for missing sheets, invalid matrices, and validation errors
- Context manager functionality tests
- Integration test with actual master input file format

### Summary of Completed Work

✅ **MasterExcelReader Implementation**
- Created adapter class with context manager support
- Implemented `read_tech_switches()` method with full validation
- Added `ExtractionResult` dataclass for structured responses
- Integrated with existing `MasterExcelValidator`

✅ **SimulationConfig Integration**
- Modified `ModelRun.run()` to check `USE_MASTER_EXCEL` flag
- Automatic extraction when master Excel file is present
- Graceful fallback to standard data files
- Comprehensive logging of extraction process

✅ **Test Coverage**
- 11 test cases covering success and error scenarios
- Mock Excel file fixtures for isolated testing
- Validation of generated CSV output

### Validation Requirements

The "Allowed tech switches" sheet must:
- Be a square matrix (same technologies in rows and columns)
- Use "YES" for allowed transitions, empty cells for disallowed
- Technology names must match those used in the plant data

### Known Technologies
Based on the existing system, expected technologies include:
- BF (Blast Furnace)
- BOF (Basic Oxygen Furnace)
- DRI (Direct Reduced Iron)
- EAF (Electric Arc Furnace)
- ESF (Electric Smelting Furnace)
- MOE (Molten Oxide Electrolysis)
- OHF (Open Hearth Furnace)

### Next Data Fields to Migrate
After tech_switches, the recommended order based on complexity:
1. **carbon_costs_xlsx** - Simple year/country/cost mapping
2. **mine_data_excel** - Straightforward supplier data
3. **technology_lcop_csv** - Technology cost parameters
4. **plants_json_path** - Complex transformation to JSON repository format

## Next Steps

### Immediate Priority: Implement Remaining Data Extractors
1. **Plants Data** (HIGH PRIORITY):
   - Read "Iron and steel plants" sheet
   - Transform to JSON repository format
   - Map capacity columns to furnace groups
   
2. **Bill of Materials** (HIGH PRIORITY):
   - Extract BOM data with proper structure
   - Handle multi-level headers
   - Convert to expected CSV format

3. **Technology Details** (MEDIUM PRIORITY):
   - Extract technology parameters
   - Validate against known technologies
   - Generate technology_lcop.csv

4. **Update ModelRun**:
   - Add mappings for all extracted files
   - Remove reliance on core-data for migrated fields

### Architecture Improvements Needed
1. **MasterExcelReader.extract_all_data()**:
   - Currently only calls read_tech_switches()
   - Need to add calls to all extraction methods

2. **File Name Mapping**:
   - Need consistent mapping between extraction methods and config fields
   - Consider using a registry pattern

## Quick Start Guide

### For Developers

1. **Enable master input mode**:
   ```python
   # In global_variables.py
   USE_MASTER_EXCEL = True
   ```

2. **Test with CLI**:
   ```bash
   # Download master input
   steelo-data-download --package master-input
   
   # Validate only
   steelo-data-prepare --use-master-input --validate-only
   
   # Full preparation
   steelo-data-prepare --use-master-input
   ```

3. **Test with Django**:
   - Upload master Excel file when creating ModelRun
   - Validation errors appear immediately
   - Check DataPreparation status for processing progress

### For Users

1. **Download template**: 
   - Click "Download Master Input Template" button in Django UI
   - Always get the latest version (served from S3 or bundled)
2. **Fill in data**: Follow the instructions sheet in the Excel file
3. **Upload**: Use Django web interface to upload and validate
4. **Run simulation**: If validation passes, proceed with simulation

## Work In Progress / Claims 🚧

To avoid conflicts, please update this section when starting work:

| Data Type | Sheet Name | Developer | Status | Started |
|---|---|---|---|---|
| Transportation costs | Transportation costs | **UNCLAIMED** | Worksheet exists, needs reader | - |
| Technology LCOP | Techno-economic details | **UNCLAIMED** | Needs implementation | - |
| Plants data | Iron and steel plants | **UNCLAIMED** | Partial implementation | - |

## Migration Summary

### Progress Overview
- **Total core-data files**: 17
- **Files successfully migrated**: 6 (35%) - including transport emissions & biomass
- **Files ready for implementation**: 7 (41%)
- **Files needing analysis**: 3 (18%)
- **Files to be removed**: 1 (geolocator_raster.csv)

### Key Architectural Improvements (2025)
1. **Module Refactoring**: Excel reader split into domain-specific modules
2. **Recreation System**: Separated recreation functions from CLI dependencies
3. **Configuration System**: Modular recreation configuration with FileRecreationSpec
4. **Master Excel Reader**: Extended with read_plants(), read_bom(), read_carbon_storage()
5. **Coordinate-based ISO3**: Added support for deriving ISO3 from coordinates

### Effort Estimate
- **High Priority Tasks**: ~16 hours (some partially complete)
- **Medium Priority Tasks**: ~10 hours
- **Low Priority Tasks**: ~8 hours
- **Total Remaining Effort**: ~34 hours

### Next Steps Priority
1. Implement high-priority extractors (plants, BOM, demand, costs)
2. Handle production history consolidation
3. Analyze and decide on cost_of_x.json
4. Complete remaining mappings
5. Full integration testing

## Files No Longer Needed in Core-Data Archive

### Files Already Migrated to Master Excel ✅
These files are now extracted from the master Excel and no longer need to be in core-data:
- `tech_switches_allowed.csv` - Extracted from "Allowed tech switches" sheet
- `railway_costs.json` - Extracted from "Railway cost" sheet
- `carbon_costs_iso3_year.xlsx` - Extracted from "Carbon costs" sheet
- `countries.csv` - Extracted from "Country mapping" sheet

### Files to be Removed 🗑️
These files will be generated on the fly or are obsolete:
- `geolocator_raster.csv` - Will be generated dynamically
- `regional_input_costs.json` - This is a derived file created from other sources

### Files Still Required in Core-Data Archive 📦
Until fully migrated, these files must remain in the archive:
1. `steel_plants_input_data_2025-03.csv` - Partial implementation exists
2. `historical_production_data.csv`
3. `iron_production_2019to2022.csv`
4. `steel_production_2019to2022.csv`
5. `technology_lcop.csv`
6. `2025_05_27 Demand outputs for trade module.xlsx`
7. `BOM_ghg_system_boundary_v6.xlsx` - Partial implementation exists
8. `cost_of_x.json`
9. `gravity_distances_dict.pkl`
10. `input_costs_for_python_model.csv`
11. `mining_data.xlsx`
12. `Data_collection_ultimate_steel.xlsx`
13. `Regional_Energy_prices.xlsx`

### Additional Master Excel Features ✨
The master Excel now includes sheets not previously in core-data:
- **CO2 storage** - `read_carbon_storage()` method implemented in MasterExcelReader
- **Biomass availability** - ✅ Fully implemented with reader, repository, and constraints
- **Power grid data** - Partial implementation exists
- **Cost of capital** - Recreation function exists
- **Trade bloc definitions** - Can be extracted from mappings
- **Subsidies** - Recreation function exists
- **Legal Process connectors** - Module created in excel_reader/legal_process.py
- **Transportation costs** - ⚠️ Worksheet added but **NO READER FUNCTION YET** (costs still hardcoded in SimulationConfig)
- **Transport emissions** - ✅ Fully implemented with reader and repository