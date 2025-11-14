# Biomass Simulation Fix Log (2025-11-14)

This note tracks all issues observed while running the biomass master input through the STEEL-IQ standalone app, plus the fixes we applied. Use it as a reference when new biomass-related failures pop up.

## 1. Missing fallback material costs (Excel issue)
- **Symptom**: Simulation aborted immediately with `ValueError: No fallback material costs loaded from fixtures`.
- **Root cause**: The "Fallback material cost" sheet in `master_input_biomass.xlsx` contained formulas that evaluated to `#VALUE!`, so the data-prep step produced an empty `fallback_material_costs.json`.
- **Fix**: Open the workbook in Excel 365+, force a recalculation, and save so numeric values populate the sheet before running data prep again. (No code change.)

## 2. Trade LP debug `exit()` left behind
- **Symptom**: Worker process died silently right after `Number of secondary feedstock constraints...` log lines.
- **Root cause**: A stray `exit()` (plus debug `print`s) remained at the end of `set_up_steel_trade_lp`, terminating the worker before the solver ran.
- **Fix**: Remove the `exit()` / debug prints so the function returns normally.

## 3. Synthetic secondary-feedstock suppliers missing
- **Symptom**: During 2042 LP extraction: `KeyError: 'bio-pci_supply_process_center'` when converting solver allocations to domain objects.
- **Root cause**: We add dummy process centers to enforce secondary-feedstock constraints, but never registered matching `Supplier` objects in the repository, so `repository.suppliers.get(...)` failed whenever the LP produced allocations from that dummy node.
- **Fix**: Introduced `_ensure_secondary_feedstock_supplier` that registers (or updates) a `Supplier` for each constraint-friendly commodity; invoked when the dummy process center is created.

## 4. Supplier capacity missing in subsequent years
- **Symptom**: Early-cycle crash (`KeyError: 2026`) when calling `add_suppliers_as_process_centers` the next year.
- **Root cause**: The synthetic suppliers persisted across years but only had `capacity_by_year` populated for the year they were first created (2025). On the next cycle the generic supplier loader tried to access `capacity_by_year[Year(2026)]` before the constraints code updated them.
- **Fix**: (a) Ensure the synthetic suppliers are refreshed for the current year *before* calling `add_suppliers_as_process_centers`, and (b) fall back to a warning if any other supplier lacks capacity for the requested year.

## 5. BOM reconstruction mismatch for bio-pci
- **Symptom**: Several years into the run: `KeyError: Input effectiveness for feedstock 'bio-pci' not found for technology BF_CHARCOAL...`
- **Root cause**: The LP and averaging logic correctly recorded `bio-pci` as part of the BF_CHARCOAL BOM, but `get_bom_from_avg_boms` only copied metallic-charge inputs from the dynamic feedstocks into `input_effectiveness`. Secondary feedstocks (bio-pci, burnt lime, etc.) were ignored, so BOM rebuilding failed.
- **Fix**: Extend `input_effectiveness` to include secondary feedstock requirements (with the same kg→t conversion we already use elsewhere) and add `bio_pci` to the conversion allowlist. Added `tests/unit/test_bom_secondary_feedstock.py` to pin this behaviour.

## Outstanding watchpoints
- Keep an eye on other secondary feedstocks that might still be recorded in kg/t (burnt lime, burnt dolomite, etc.). If future failures point at them, add them to `SECONDARY_FEEDSTOCKS_REQUIRING_KG_TO_T_CONVERSION`.
- The LP still warns when a supplier lacks capacity for the current year; if that warning fires for real suppliers, we need to backfill their annual capacity curves in the data prep step.

