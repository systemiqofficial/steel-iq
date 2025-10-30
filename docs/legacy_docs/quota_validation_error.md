# Quota Validation Error Analysis

## Problem Summary
When running the simulation with the correct quota master input file, the simulation fails with:
```
pydantic_core._pydantic_core.ValidationError: 1 validation error for TariffListInDb
root.8749.tax_percentage
  Input should be a valid number [type=float_type, input_value=None, input_type=NoneType]
```

## Root Cause Analysis

### 1. The China Quota Entry
The last entry in `tariffs.json` (index 8749) is:
```json
{
  "tariff_id": "6c84452e-d1c4-4594-b001-589585ea12b4",
  "tariff_name": "just_a_regular_everyday_normal_tariff",
  "from_iso3": "CHN",
  "to_iso3": "*",
  "tax_absolute": null,
  "tax_percentage": null,  // <-- This is the problem
  "quota": 0.0,
  "start_date": 2020,
  "end_date": 2100,
  "metric": "",
  "commodity": "*"
}
```

### 2. Model Mismatch
There's a mismatch between the domain model and the Pydantic serialization model:

**Domain Model** (`src/steelo/domain/models.py`):
```python
class TradeTariff:
    def __init__(
        self,
        ...
        tax_percentage: float | None = None,  # Optional
        ...
    )
```

**Pydantic Model** (`src/steelo/adapters/repositories/json_repository.py`):
```python
class TariffInDb(BaseModel):
    ...
    tax_percentage: float  # Required! Not optional
    ...
```

### 3. Why This Happened
- The Excel file likely had an empty cell for "Tax [%]" in the China quota row
- This was read as `None` in Python and saved as `null` in JSON
- The domain model allows this (quota-only tariffs with no tax)
- But the Pydantic model requires a float value

## Solutions

### ✅ Applied Fix (Code Change)
Updated the Pydantic model to match the domain model in `src/steelo/adapters/repositories/json_repository.py`:

```python
class TariffInDb(BaseModel):
    """
    Pydantic model for serializing/deserializing a TradeTariff to/from JSON.
    """
    tariff_id: str
    tariff_name: str
    from_iso3: str
    to_iso3: str
    tax_absolute: float | None
    tax_percentage: float | None  # Changed from float to float | None
    quota: Volumes | None
    start_date: Year
    end_date: Year
    metric: str
    commodity: str
```

This fix:
- Allows tariffs with only quotas (no tax) to be properly handled
- Matches the domain model's design where tax_percentage is optional
- Works automatically without user intervention
- Maintains backward compatibility with existing tariff entries

### Why This Fix is Correct
1. The domain model (`TradeTariff`) already supports optional tax_percentage
2. The trade module already checks `isinstance(trade_tariff.tax_percentage, float)` before using it
3. It's logical to have quota-only tariffs (like export bans) without taxes
4. The fix aligns the serialization layer with the business logic

## Verification Steps
1. Check if other entries have null tax_percentage: 
   ```bash
   jq '.root[] | select(.tax_percentage == null)' tariffs.json
   ```
2. Verify the fix works by running the simulation
3. Consider adding validation in the Excel reader to prevent this in the future