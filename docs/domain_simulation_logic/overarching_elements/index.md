# Model Overarching Elements

These parts of the simulation are not tied to one model. They provide the shared context, inputs and outputs that the Trade Model (TM), the Plant Agent Model (PAM) and the Geospatial Model (GEO) all use.

- **[Environment](environment.md)**: the macro-scale context of the simulation. It tracks system-wide variables such as production capacity, cost curves and market price forecasts, and aggregates data from furnace groups, plants and demand centres.
- **[Sub-national Geography](sub_national_geography.md)**: how inputs can be set for a province instead of a whole country, so plants in the same country can face different electricity prices, hydrogen costs, subsidies and grid emissions.
- **[Energy Prices of Existing and New Plants](energy_prices.md)**: how electricity and hydrogen prices reach existing plants and a new plant, from its candidate site to operation, and which stage of the energy-price trace watches each step.
- **[Checkpoint System](checkpoint.md)**: saving and restoring the simulation state at regular intervals, for recovery and debugging.
- **[Outputs and Post-Processing](outputs_and_postprocessing.md)**: the files a simulation produces, from traces during the run to plots and the post-processed CSVs.

```{toctree}
:hidden:

environment
sub_national_geography
energy_prices
checkpoint
outputs_and_postprocessing
```
