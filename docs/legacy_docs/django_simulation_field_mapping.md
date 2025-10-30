# Django Form to Simulation Field Mapping

This document tracks the connection status of fields from the Django ModelRunCreateForm to the steel simulation model.

## Overview

The Django web interface collects ~60+ configuration parameters through the ModelRunCreateForm, but currently only a small subset of these are actually passed to and used by the simulation. This document tracks:

1. Which fields are fully connected
2. Which fields are partially connected
3. Which fields are not yet connected
4. Implementation notes for each field
5. Priority levels based on Steel Development Progress Tool

## Connection Status Legend

- ✅ **Connected**: Field is collected in form, passed to simulation, and used in calculation
- 🟡 **Partial**: Field is collected and passed but not fully utilized
- ❌ **Not Connected**: Field is collected in form but not passed to simulation
- 🚧 **In Progress**: Currently being implemented

## Priority Levels (from Steel Development Progress Tool)

- **P1**: 1st priority - non-negotiable (must be implemented)
- **P2**: 2nd priority - very welcome (highly desirable)
- **P3**: 3rd priority - nice to have (good additions if time permits)
- **P4**: 4th priority - not needed before final (can wait)

## Summary Statistics

### Connection Status by Priority:
- **P1 Fields**: 4/29 connected (14%)
- **P2 Fields**: 0/13 connected (0%)
- **Total**: 4/42 connected (10%)

### Fields by Category:
- **Time Period**: 2/2 connected ✅
- **Scenarios**: 1/6 connected 
- **Economic**: 0/4 connected
- **Technology**: 0/22 connected (global_bf_ban removed)
- **Geospatial**: 0/8 connected
- **File Inputs**: 1/8 connected

## Field Mapping Status

### Time Period Settings

| Field | Form Name | Config Key | Simulation Usage | Status | Priority | Notes |
|-------|-----------|------------|------------------|--------|----------|-------|
| Start Year | `start_year` | `start_year` | `SimulationConfig.start_year` | ✅ | P1 | Fully connected |
| End Year | `end_year` | `end_year` | `SimulationConfig.end_year` | ✅ | P1 | Fully connected |

### Scenario Settings

| Field | Form Name | Config Key | Simulation Usage | Status | Priority | Notes |
|-------|-----------|------------|------------------|--------|----------|-------|
| Total Steel Demand Scenario | `total_steel_demand_scenario` | `total_steel_demand_scenario` | Not used in SimulationRunner | ❌ | P1 | Only works with NBSimulationRunner (Excel-based), not with SimulationRunner (JSON-based) used by Django |
| Green Steel Demand Scenario | `green_steel_demand_scenario` | `green_steel_demand_scenario` | Not used | ❌ | P1 | Form has options, not passed to simulation |
| Scrap Generation Scenario | `scrap_generation_scenario` | `scrap_generation_scenario` | `global_variables.CHOSEN_DEMAND_SCENARIO` | ✅ | P1 | Connected as of 2025-01-06, form shows only "BAU" and "High circularity" options |
| Trade Scenario | `trade_scenario` | `trade_scenario` | Not used | ❌ | P2 | Form has 6 options, not passed |
| Trade Tariffs | `trade_tariffs` | `trade_tariffs` | Not used | ❌ | P2 | Boolean field, not passed |
| Emissions Boundary | `emissions_boundary` | `emissions_boundary` | Not used | ❌ | P2 | Form has 2 options, not passed |

### Economic Parameters

| Field | Form Name | Config Key | Simulation Usage | Status | Priority | Notes |
|-------|-----------|------------|------------------|--------|----------|-------|
| Carbon Tax Enable | `enable_carbon_tax` | `enable_carbon_tax` | Not used | ❌ | P1* | *Elevated to P1 if easy to implement |
| Carbon Tax Amount | `carbon_tax_amount` | `carbon_tax_amount` | Not used | ❌ | P1* | USD/tCO2, *elevated to P1 if easy |
| Capital Costs | `capital_cost_scenario` | `capital_cost_scenario` | Not used | ❌ | P1 | Percentage adjustment, not passed |
| WACC | `wacc` | `wacc` | Not used | ❌ | P1 | Percentage value, not passed |

### Technology Availability

For each technology (BF, BOF, DRI-NG, DRI-H2, EAF, ESF, MOE, Electrowinning), the following fields exist:

| Field Pattern | Form Name | Config Key | Simulation Usage | Status | Priority | Notes |
|-------|-----------|------------|------------------|--------|----------|-------|
| Allowed | `{tech}_allowed` | `{tech}_allowed` | Not used in SimulationRunner | ❌ | P1 | Was implemented in NBSimulationRunner (now removed) |
| From Year | `{tech}_from_year` | `{tech}_from_year` | Not used in SimulationRunner | ❌ | P1 | Was implemented in NBSimulationRunner (now removed) |
| To Year | `{tech}_to_year` | `{tech}_to_year` | Not used in SimulationRunner | ❌ | P1 | Was implemented in NBSimulationRunner (now removed) |
| Cost Scenario | `{tech}_cost_scenario` | `{tech}_cost_scenario` | Not used | ❌ | P2 | 3 options per tech, not passed |

Additional technology fields:
| Field | Form Name | Config Key | Simulation Usage | Status | Priority | Notes |
|-------|-----------|------------|------------------|--------|----------|-------|
| BF+CCS Allowed | `bf_ccs_allowed` | `bf_ccs_allowed` | Not used | ❌ | P2 | Boolean, not passed |
| DRI-NG+CCS Allowed | `dri_ng_with_ccs_allowed` | `dri_ng_with_ccs_allowed` | Not used | ❌ | P2 | Boolean, not passed |
| Global BF Ban | `global_bf_ban` | `global_bf_ban` | **REMOVED** - Use technology_settings | ❌ | P1 | **DEPRECATED**: Use technology_settings to control BF availability |

### Geospatial & Infrastructure

| Field | Form Name | Config Key | Simulation Usage | Status | Priority | Notes |
|-------|-----------|------------|------------------|--------|----------|-------|
| Power Prices | `power_price_file` | `power_price_file` | Not used | ❌ | P1 | Dropdown options, affects plant location priority |
| Infrastructure Buildout | `infrastructure_buildout` | `infrastructure_buildout` | Not used | ❌ | P1 | Dropdown options, affects plant location priority |
| Transport Costs | `transport_costs` | `transport_costs` | Not used | ❌ | P1 | Dropdown options, affects plant location priority |
| Land Use/Cover | `land_use` | `land_use` | Not used | ❌ | P1 | Dropdown options, affects plant location priority |
| Max Slope | `max_slope` | `max_slope` | Not used | ❌ | P1 | Percentage field, geospatial constraint |
| Max Altitude | `max_altitude` | `max_altitude` | Not used | ❌ | P1 | Meters field, geospatial constraint |
| H2 Scenario | `h2_scenario` | `h2_scenario` | Not used | ❌ | P2 | 3 options, not passed |
| Renewable Scenario | `renewable_scenario` | `renewable_scenario` | Not used | ❌ | P2 | 3 options, not passed |

### File Inputs

| Field | Form Name | Config Key | Simulation Usage | Status | Priority | Notes |
|-------|-----------|------------|------------------|--------|----------|-------|
| Plants Repository | `plants_repository` | `plants_json_path` | `SimulationConfig.plants_json_path` | ✅ | P1 | Fully connected |
| Circularity File | `circularity_file` | `circularity_file_path` | Not used | ❌ | P2 | Dropdown/upload option |
| Input Costs File | `input_costs_file` | `input_costs_file` | Not used | ❌ | P1 | For adjusting nat gas, coke costs |
| Carbon Tax File | `carbon_tax_file` | `carbon_tax_file` | Not used | ❌ | P1* | *P1 if carbon tax is easy |
| Trade Policy File | `trade_policy_file` | `trade_policy_file` | Not used | ❌ | P2 | Trade scenarios |
| Power Price File | `power_price_file` | `power_price_file` | Not used | ❌ | P1 | Geospatial module |
| Capital Costs File | `capital_costs_file` | `capital_costs_file` | Not used | ❌ | P1 | CAPEX adjustments |
| Resource Availability File | `resource_availability_file` | `resource_availability_file` | Not used | ❌ | P2 | Resource constraints |

### Hidden/System Fields

| Field | Form Name | Config Key | Simulation Usage | Status | Priority | Notes |
|-------|-----------|------------|------------------|--------|----------|-------|
| Output File | N/A | `output_file` | `SimulationConfig.output_file` | ✅ | N/A | Auto-generated |
| Log Level | N/A | `log_level` | `SimulationConfig.log_level` | ✅ | N/A | Default value used |

## Implementation Guide

### To Connect a New Field

1. **Add to SimulationConfig** (if needed):
   ```python
   # In src/steelo/simulation.py
   @dataclass
   class SimulationConfig:
       new_field: str = "default_value"
   ```

2. **Add to expected_params in ModelRun.run()**:
   ```python
   # In src/steeloweb/models.py
   expected_params = {
       # ... existing params ...
       "new_field",
   }
   ```

3. **Use in NBSimulationRunner**:
   ```python
   # In src/steelo/simulation.py, NBSimulationRunner.setup()
   # Set global variables or pass to appropriate components
   ```

4. **Write Tests**:
   - Unit test in `tests/unit/`
   - Integration test in `tests/web/`

5. **Update this documentation**

## Priority Fields for Connection

Based on the Steel Development Progress Tool, fields should be connected in this order:

### P1 - Non-negotiable (Must implement first):
1. **Demand Scenarios** (Total Steel & Green Steel) - Core functionality
2. **Technology Availability** (allowed/from/to year for each tech) - Critical for scenario modeling
3. **Geospatial Module** (all 6 fields) - Affects plant location decisions
4. **Input Costs Adjustment** - Economic calculations
5. **Capital Costs & WACC** - Financial parameters
6. **Carbon Tax** (*elevated to P1 if easy to implement) - Economic impact

### P2 - Very Welcome (Implement second):
1. **Trade Scenario & Tariffs** - Trade modeling
2. **Technology Cost Scenarios** - Fine-tuning economics
3. **CCS Technologies** (BF+CCS, DRI-NG+CCS) - Advanced options
4. **H2 & Renewable Scenarios** - Energy transition modeling
5. **Emissions Boundary** - Carbon accounting

### P3 - Nice to Have:
(No fields currently marked as P3 in mapping)

### P4 - Not needed before final:
(No fields currently marked as P4 in mapping)

## Implementation Notes

### Technology Availability (Not Connected in SimulationRunner)
- **Issue**: Technology availability settings were implemented in NBSimulationRunner (now removed)
- **How it would work**: 
  - Updates `TECHNOLOGY_ACTIVATION_YEAR` based on `{tech}_from_year` settings
  - Stores `{tech}_to_year` in `env.technology_phase_out_year` 
  - Filters `allowed_furnace_transitions` to remove transitions TO disabled technologies
  - Filters capex to exclude technologies past their phase-out year
  - Does NOT remove existing furnace groups - controls what technologies can be switched TO
- **Technology Mapping**:
  - `bf` → "BF" (Blast Furnace)
  - `bof` → "BOF" (Basic Oxygen Furnace)  
  - `dri_ng` → "DRI" (Direct Reduced Iron - Natural Gas)
  - `dri_h2` → "DRI-H2" (Direct Reduced Iron - Hydrogen)
  - `eaf` → "EAF" (Electric Arc Furnace)
  - `esf` → "ESF" (Electric Smelting Furnace)
  - `moe` → "MOE" (Molten Oxide Electrolysis)
- **Required Fix**: Needs to be reimplemented in SimulationRunner or at data preparation stage

## Special Notes from Excel

- **Carbon Tax Priority**: The Excel explicitly states that the global carbon tax feature should be **"[if easy - push up to 1st priority]"**. This suggests it should be one of the first P2 features investigated for potential elevation to P1.
- **Dashboard Design**: The form should ideally fit on one screen as a user-friendly dashboard
- **Code Connections**: Several fields directly map to `global_variables.py`:
  - Years → `SIMULATION_START_YEAR`, `SIMULATION_END_YEAR`
  - Demand scenario → `CHOSEN_DEMAND_SCENARIO`
  - Geospatial parameters → "GEO User Tunning" section

## General Notes

- Many form fields were designed for future functionality
- Some fields may require significant refactoring to properly integrate
- Consider grouping related fields into nested configuration structures
- Some global variables may need to be refactored to accept dynamic values

### Total Steel Demand Scenario (Not Connected - 2025-06-23)
- **Issue**: Django uses SimulationRunner which loads from JSON repositories, not Excel files
- **Required Fix**: 
  - Need to implement scenario filtering at data preparation stage when JSON files are created
  - Or modify SimulationRunner to support dynamic scenario selection
- **Scenario Mapping** (for future implementation):
  - `business_as_usual` → "BAU"
  - `system_change` → "System change"
  - `accelerated_transition` → "Accelerated transition"
  - `climate_neutrality` → "Climate neutrality"
  - `high_efficiency` → "High efficiency"
  - `circular_economy` → "High circularity"
  - `technology_breakthrough` → "Technology breakthrough"