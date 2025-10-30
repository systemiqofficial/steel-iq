# Create Model Run Form Native Validation

```
Status: Active
Date: 2025-02-14
```

## Context

The **Create Simulation** form relies on the browser's built-in HTML5 validation for
numeric fields such as `construction_time` and `consideration_time`. Users expect
the usual tooltip (for example, "Value must be less than or equal to 10") when a
value falls outside the field's `min`/`max` bounds.

## Issue

Browsers block form submission **before** emitting a `submit` event if any control
fails constraint validation. Because the DOMContentLoaded handler in
`src/steeloweb/templates/steeloweb/create_modelrun.html` only ran its logic inside a
`submit` callback, the invalid controls stayed focused and no feedback was shown.
The form silently refreshed, leaving users unaware that the value was rejected.

## Resolution

The template now:

1. Listens for `invalid` events in the capture phase.
2. Calls `reportValidity()` on the failing control, ensuring the native tooltip
   appears even when the browser suppresses `submit`.
3. Displays a Bootstrap-style inline error message (`.client-invalid-feedback`) so
   the rejection remains visible after the scroll.
4. Clears the inline message on `input` once the field returns to a valid state.

## Maintenance Notes

- Do **not** remove the `invalid` event handler or the helper functions around
  it; without them the browser will again suppress the message for focused number
  fields.
- If the form markup moves, ensure the helper still locates the appropriate
  container (`.col-sm-9`) so inline messages render in the correct spot.
- Additional validation hooks (HTMX swaps, resets, etc.) should reuse the same
  utilities rather than duplicating the logic elsewhere.

Keeping these pieces in place preserves both accessibility (ARIA updates) and the
expected native tooltip behaviour.
