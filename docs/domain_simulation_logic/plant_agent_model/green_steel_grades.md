# Green Steel Grades

## Overview

Green steel grades classify steel production based on emissions intensity and scrap share. The grading system uses linear threshold functions to determine qualification levels, with Level 1 being the most stringent certification.

## Grade Definitions

The system uses four grade levels with the following thresholds:

| Grade | Threshold function (y <= b - m*x) parameter b | Threshold function (y <= b - m*x) parameter m | Definition of x |
|-------|----------------------------------------------|----------------------------------------------|-----------------|
| Level 1 | 2.8 | 2.3 | Scrap share in overall iron input |
| Level 2 | 2 | 1.65 | Scrap share in overall iron input |
| Level 3 | 1.2 | 1 | Scrap share in overall iron input |
| Level 4 | 0.4 | 0.35 | Scrap share in overall iron input |

### Threshold Function

Each grade uses a linear threshold function:

```
y <= b - m*x
```

Where:
- **y** = emissions intensity (tCO2eq/t steel)
- **x** = scrap share as percentage of overall iron input (0-100)
- **b** = y-intercept parameter (threshold at 0% scrap)
- **m** = slope parameter (reduction per percentage point of scrap)

### Example Calculations

**Level 1 (Most Stringent):**
- At 0% scrap: emissions must be ≤ 2.8 tCO2eq/t
- At 50% scrap: emissions must be ≤ 2.8 - 2.3×0.5 = 1.65 tCO2eq/t
- At 100% scrap: emissions must be ≤ 2.8 - 2.3×1.0 = 0.5 tCO2eq/t

**Level 4 (Least Stringent):**
- At 0% scrap: emissions must be ≤ 0.4 tCO2eq/t
- At 50% scrap: emissions must be ≤ 0.4 - 0.35×0.5 = 0.225 tCO2eq/t
- At 100% scrap: emissions must be ≤ 0.4 - 0.35×1.0 = 0.05 tCO2eq/t

## Implementation Details

### Data Model

The `GreenSteelGrade` class (in `src/steelo/domain/models.py`) represents each grade level:

```python
class GreenSteelGrade:
    level: int  # 1, 2, 3, or 4
    name: str   # "Level 1", "Level 2", etc.
    b: float    # y-intercept parameter
    m: float    # slope parameter

    def check_threshold(self, emissions_intensity: float, scrap_share: float) -> bool:
        """Check if emissions and scrap share meet this grade's threshold"""
        threshold = self.b - self.m * scrap_share
        return emissions_intensity <= threshold
```

### Furnace Group Methods

Each `FurnaceGroup` can calculate its green steel qualification:

```python
class FurnaceGroup:
    def calculate_emissions_intensity(self) -> float:
        """Calculate total emissions per ton of steel output"""
        # Returns tCO2eq/t including all scopes

    def calculate_scrap_share(self) -> float:
        """Calculate percentage of scrap in iron inputs"""
        # Returns 0-100 based on bill of materials

    def get_green_steel_grade(self, environment: Environment) -> Optional[int]:
        """Determine best qualifying green steel grade"""
        # Returns 1-4 or None if no qualification
```

### Key Rules

1. **Production Required**: Only furnace groups with positive production (`allocated_volumes > 0`) can qualify
2. **Best Grade Wins**: If multiple grades are met, the lowest (best) number is returned
3. **All Scopes Included**: Emissions intensity includes Scope 1, 2, and 3 emissions

## Data Input

Green steel definitions are read from the **"Green Steel Definitions"** sheet in the Master Excel file during data preparation.

### Required Excel Format

The sheet must contain these column headers:
- `Grade` (or containing "grade")
- Column containing "parameter b" (but not ending with "parameter m")
- Column ending with "parameter m"
- Column with "definition of x" (optional, for documentation)

## Grade Assignment Logic

```python
# For each furnace group with production:
emissions_intensity = calculate_emissions_intensity()  # tCO2eq/t
scrap_share = calculate_scrap_share()  # 0-100%

applicable_grades = []
for level, grade in green_steel_grades.items():
    threshold = grade.b - grade.m * scrap_share
    if emissions_intensity <= threshold:
        applicable_grades.append(level)

# Return best (lowest) grade or None
return min(applicable_grades) if applicable_grades else None
```

## Logging and Monitoring

### Individual Furnace Logging

When a furnace group qualifies:
```
[GREEN STEEL] P000001_FG1: Grade 1 (emissions: 1.20 tCO2e/t, scrap: 60.0%)
```

### Summary Statistics

The Environment provides aggregate reporting:

```python
environment.log_green_steel_summary(plants, year=2030)
```

Output example:
```
============================================================
[GREEN STEEL SUMMARY] Year 2030
============================================================
Total active furnace groups: 500
Qualified for green steel: 150 (30.0%)
Total production: 1200.5 Mt
Green steel production: 360.2 Mt (30.0%)

Grade distribution:
  Level 1: 25 furnace groups, 50.3 Mt production
  Level 2: 45 furnace groups, 120.5 Mt production
  Level 3: 50 furnace groups, 130.2 Mt production
  Level 4: 30 furnace groups, 59.2 Mt production
============================================================
```

## Integration Points

1. **Data Loading**: `src/steelo/data/recreate.py` - Reads from Excel and saves to JSON
2. **Excel Reader**: `src/steelo/adapters/dataprocessing/excel_reader.py` - Parses green steel sheet
3. **Environment**: Stores grade definitions and provides summary reporting
4. **FurnaceGroup**: Calculates qualification during simulation

## Technical Notes

### Performance
- Emissions and scrap calculations are performed on-demand
- No caching currently implemented
- Summary logging iterates all plants once per call

### Backward Compatibility
- Missing green steel sheet returns empty dictionary
- Simulations run normally without grade definitions
- No impact on existing functionality

### Validation
- Grade strictness ordering checked (Level 1 most strict)
- Parameters must be numeric
- Scrap share constrained to 0-100%
- Emissions intensity must be non-negative