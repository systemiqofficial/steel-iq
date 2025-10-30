# Technical Debt Documentation

This document tracks known technical debt and areas for future refactoring in the Steel Model codebase.

## High Priority

### 1. Steel Plant Lifetime Configuration (Added: 2025-01-09)

**Current Implementation:**
- `STEEL_LIFETIME` is a hardcoded constant in `src/steelo/domain/constants.py` set to 20 years
- Used in 50+ locations throughout domain models for:
  - Plant lifetime calculations
  - Renovation cycles  
  - Debt repayment periods
  - Storage reservations
  - NPV calculations

**Temporary Workaround:**
- In `bootstrap.py`, we dynamically update the global constant based on `SimulationConfig.steel_plant_lifetime`
- This allows the value to be configurable via the Django UI
- See implementation in `src/steelo/bootstrap.py:74-78`

**Issues with Current Approach:**
- Modifies global state which could affect parallel simulations
- Violates domain model purity principles
- Not thread-safe

**Recommended Solution:**
- **Option A (Preferred):** Add lifetime as a property to Plant/FurnaceGroup models
  - Pass from config when creating plants
  - More object-oriented and cleaner design
  - Requires refactoring all 50+ usage locations
  
- **Option B:** Pass lifetime through Environment to domain methods
  - Would require modifying many method signatures
  - Breaks domain separation concerns

**Affected Files:**
- `src/steelo/domain/models.py` (primary usage)
- `src/steelo/domain/constants.py` (constant definition)
- `src/steelo/bootstrap.py` (temporary workaround)
- `src/steelo/simulation.py` (config definition)

**Tracking:**
- Issue created: 2025-01-09
- Priority: High (affects simulation accuracy and user configuration)

### 2. Oversized Domain Models File (Added: 2025-01-09)

**Current Implementation:**
- `src/steelo/domain/models.py` contains **6,818 lines of code** (315KB)
- 29 class definitions in a single file
- 288 method definitions across all classes
- Critical classes like `Environment` (1,985 lines), `FurnaceGroup` (1,415 lines), and `Plant` (1,088 lines) dominate the file

**Issues:**
- Violates Single Responsibility Principle at the module level
- Makes code reviews difficult and error-prone
- Increases cognitive load for developers
- Slows down IDE performance and indexing
- Creates merge conflicts more frequently
- Testing individual components is complex

**Challenges for Refactoring:**
- **Tight coupling between classes:** Environment references most other domain objects
- **Circular dependencies:** Plant → FurnaceGroup → Environment cycles likely exist
- **Dynamic imports within methods:** 21+ internal imports of calculation modules
- **Complex state management:** Classes maintain both historical and current state
- **Backward compatibility:** Used throughout the entire codebase

**Recommended Solution:**
Split into a domain package structure:
```
src/steelo/domain/
├── base/           # Utility classes (BoundedString, TimeFrame, Location)
├── entities/       # Core business entities (Plant, FurnaceGroup, PlantGroup)
├── environment/    # Environment class (needs further splitting)
├── technology/     # Technology, Feedstock, Production models
├── finance/        # Costs, Subsidies, Financial models
├── market/         # DemandCenter, Supplier, Allocations
└── config/         # Configuration and data classes
```

**Priority:** High - This is blocking effective development and maintenance

### 3. Environment Class Refactoring (Added: 2025-01-09)

**Current Implementation:**
- Single `Environment` class with ~2,000 lines handling 15+ distinct responsibilities
- 80+ attributes storing various types of data
- 50+ methods ranging from cost calculations to technology management
- Acts as both domain service and data repository

**Responsibilities Currently Mixed:**
1. Configuration management
2. Financial modeling (CAPEX, cost of capital, debt)
3. Market economics (cost curves, price extraction)
4. Carbon cost management
5. Energy cost management
6. Material/feedstock management
7. Technology management
8. Regional data management
9. Trade management (tariffs, transport)
10. Constraint management
11. Demand forecasting
12. Cost curve generation
13. Data initialization (20+ `initiate_*` methods)
14. Plant/furnace group operations
15. Geospatial data management

**Issues:**
- Textbook example of God Object anti-pattern
- Violates Single Responsibility Principle
- High coupling with Plant and FurnaceGroup classes
- Mixed abstractions (low-level calculations with high-level orchestration)
- Complex nested state dictionaries
- Difficult to test individual responsibilities

**Challenges for Refactoring:**
- **Data dependencies:** Complex nested dictionaries need careful migration
- **Circular references:** Environment modifies Plants while depending on them
- **Method orchestration:** Many methods coordinate multiple operations
- **Configuration vs runtime state:** Mixed immutable config with mutable state
- **High integration points:** Used throughout handlers, message bus, checkpointing

**Recommended Solution:**
Decompose into focused services:
- Core Environment (minimal state tracking)
- CapexService, CostOfCapitalService, SubsidyService (financial)
- CostCurveService, PricingService, DemandForecastService (market)
- TechnologyConfigService, FeedstockService, MaterialCostService (technology)
- RegionalDataService, TransportService (regional)
- CarbonCostService, CarbonStorageService (carbon)

Use dependency injection and event-driven patterns to reduce coupling.

**Priority:** High - Central to architecture, affects all new features

### 4. Excel Reader Module Decomposition (Added: 2025-01-09)

**Current Implementation:**
- Single `excel_reader.py` file with **2,467 lines** (103KB)
- 40+ reader functions handling different data domains
- 31 calls to `pd.read_excel()` with repeated boilerplate
- 80+ try/except blocks with inconsistent error handling
- Hardcoded column names and translation dictionaries throughout

**Issues:**
- **Code duplication:** Same patterns repeated 28+ times for DataFrame iteration
- **Tight coupling:** Functions directly depend on Excel sheet structures
- **Mixed concerns:** Data validation, transformation, and domain logic intertwined
- **Inconsistent patterns:** Some functions return objects, others update repositories
- **Large translation dictionaries:** 100+ lines of hardcoded mappings
- **Difficult to test:** Cannot easily mock or test individual readers

**Challenges for Refactoring:**
- **Complex transformations:** Some functions like `read_dynamic_business_cases()` have 200+ lines
- **Multiple format support:** Legacy vs new Excel formats handled in same functions
- **Side effects:** Some functions modify passed objects
- **State management:** Global constants mixed with business logic

**Recommended Solution:**
Create a structured package:
```
src/steelo/adapters/dataprocessing/excel_readers/
├── base/           # Common patterns, error handling, validation
├── domain/         # Location, technology, plant readers
├── economic/       # Cost, tariff, subsidy readers
├── supply_chain/   # Supplier, demand, feedstock readers
├── policy/         # Regulation, mapping readers
├── technical/      # Hydrogen, transport, constraint readers
└── config/         # Externalized column mappings and translations
```

Extract common patterns into base classes, standardize error handling, and separate validation from business logic.

**Priority:** Medium - Affects data ingestion but not core simulation

### 5. MaterialParameters Enum Key Type Inconsistency (Added: 2025-09-16)

**Current Implementation:**
- `BOMElement` class type annotation expects `dict[str, float | None]` for parameters
- Throughout the codebase, parameters are accessed using string keys via `.value` (e.g., `MaterialParameters.INPUT_RATIO.value`)
- This inconsistency was hidden until mypy type checking was enforced

**Background:**
- Original type annotation incorrectly specified `dict[MaterialParameters, float]` (enum keys)
- Actual runtime code uses `MaterialParameters.*.value` (string keys) in 5+ locations
- Attempt to "fix" mypy errors in commit `b00257ad` broke simulation by using enum keys directly

**Issues:**
- **Type safety compromised:** Type annotation doesn't match actual usage pattern
- **Fragile code:** Easy to break by "fixing" type errors without understanding runtime behavior
- **Hidden coupling:** Consumer code assumes string keys while producers could use either
- **Maintenance risk:** Future developers may attempt similar "fixes"

**Current Workaround:**
- Type annotation changed to `dict[str, float | None]` to match runtime usage
- All code continues using `.value` to access enum string values
- This preserves functionality but doesn't address the fundamental inconsistency

**Recommended Solution:**
**Option A: Standardize on Enum Keys (Cleaner)**
- Change all consumers to use enum keys directly
- Remove `.value` from all parameter access code
- Update type annotation to `dict[MaterialParameters, float | None]`
- Benefits: Type-safe, IDE autocomplete, refactoring support

**Option B: Create Type-Safe Wrapper**
- Create a `BOMParameters` class with typed properties
- Encapsulate the dict and provide typed accessors
- Migrate gradually from dict to wrapper class

**Affected Files:**
- `src/steelo/domain/trade_modelling/trade_lp_modelling.py` (lines 436, 471, 940-945)
- `src/steelo/domain/trade_modelling/set_up_steel_trade_lp.py` (line 77-79)
- Any future code that creates or consumes BOM parameters

**Related Issues:**
- Commit `b00257ad` - Attempted fix that broke simulation
- Issue tracked in `ISSUE_MATERIALPARAMETERS_TYPE_MISMATCH.md`

**Priority:** Medium - Works currently but is a maintenance hazard

---

## Medium Priority

(Add other technical debt items here as discovered)

## Low Priority

(Add other technical debt items here as discovered)

## Resolved

(Move items here once addressed)