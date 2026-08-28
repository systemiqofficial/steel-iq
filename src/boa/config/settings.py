# ===== Baseload power simulation parameters =====
# Lifetime of technologies in years (note: years must be a positive integer)
LIFETIMES = {
    "solar": 25,  # IEA: 25-30 years (see README.md)
    "wind": 25,  # IRENA, p.20; other sources say 20 years (see README.md)
    "battery": 25,  # Aligned with solar/wind so no tech reinstalls within the investment horizon
}

# Learning rates for solar and wind technologies
# NOTE: currently unused in the model (already included in the input data in capex projections)
LEARNING_RATES = {
    "solar": 0.234,
    "wind": 0.146,
}

# Scaling factor for batteries: Used in the transformed capex scaling factor equation to account for modules with
# several units being installed at once being cheaper than many single units
BATTERY_UNIT_CAPEX_SCALING_FACTOR = -0.15

# Deterioration rate the energy systems over their lifetime
YEARLY_DETERIORATION_RATES = {
    "solar": 0.005,  # 0.5%/year
    "wind": 0.01,  # 1%/year
    "battery": 0.015,  # 1.5%/year; batteries degrade faster (NREL, see README.md)
    # NOTE: currently unused in the model - modify LCOE calculation to include battery deterioration explicitly
}

# Annual maintenance downtime per technology, used when computing generated electricity (reduces availability by downtime/DAYS_IN_YEAR)
# NOTE: currently only applied by the scalar reference LCOE path (cost_calculations.calculate_lcoe_of_re_installation)
# the vectorised production pricer does not model downtime.
MAINTENANCE_DOWNTIME_DAYS = 10  # days/year

# Random seed for reproducibility
RANDOM_SEED = 42

# Scale of the Monte Carlo design proposal, as a multiple of 1/CF: overscale draws come
# from Exp(mean=mu) with mu = OVERSCALE_SAMPLING_K[tech] / CF_tech (per-pixel time-mean
# capacity factor), so the search tracks the site's resource. The capacity ceiling is
# applied downstream as a query-time mask, never inside the sampler. Search-tuning knob,
# not a physical parameter; validated for baseloads up to 20,000 MW.
OVERSCALE_SAMPLING_K = {"wind": 0.75, "solar": 0.75}

# Minimum share of the n sampled designs that must clear the coverage filter before a
# pixel's LCOE argmin is trusted (threshold = ceil(fraction * n); n=2000 -> 20 designs).
# With a single survivor the "optimum" is one Monte Carlo draw, not a reproducible
# optimum, so such pixels are reported as infeasible (status 4) instead. Like
# OVERSCALE_SAMPLING_K this is a search-quality knob, not a physical parameter;
# 0.0 disables the cut, restoring the "at least one surviving design" behaviour.
MIN_SURVIVOR_FRACTION = 0.01

# Quality trigger for the query-time top-up: a pixel whose masked survivor count falls
# below this fraction of n is re-sampled from the box-truncated proposal (the adequacy
# trigger, MIN_SURVIVOR_FRACTION, always applies on top). Raising it trades query time
# for a thinner pessimistic LCOE tail on sparsely-covered pixels.
TOPUP_QUALITY_FRACTION = 0.25

# ===== Max-capacity ceiling parameters (boa_cds max-capacity) =====
# Installable capacity densities implied by the shipped max_capacity files.
# override with --wind-density until settled.
CAPACITY_DENSITY_MW_PER_KM2 = {"pv": 140, "wind": 10}

# ESA-CCI LCCS class -> usable land fraction, ported from steel-iq
# wind_and_pv/availability.py LULC_CODES.
# Unlisted classes (notably forests) get fraction 0, i.e. are fully excluded.
#
# !!! UNVALIDATED, AND THESE VALUES DOMINATE THE RESULT. RECHECK BEFORE TRUSTING A RUN. !!!
#
# Together with CAPACITY_DENSITY_MW_PER_KM2 above they imply an effective ceiling of
# roughly 1.8 MW/km2 for pv and 1.5 MW/km2 for wind on typical European land -- a 0.25 deg
# cell then tops out far below what a 500 MW baseload needs. Applied alongside the
# cds_exclusion layer, nearly every central-European land cell fails annual energy balance
# at its own ceiling, and is therefore reported infeasible before any dispatch runs.
#
# That is what the numbers here say, not a defect in the code that applies them. Whether it
# is the intended model behaviour is an open question for the team. The levers are the
# fractions below, the densities above, the baseload, and whether a plant is confined to a
# single cell at all.
LULC_CODES = {
    "pv": {
        10: 0.02,
        11: 0.02,
        20: 0.02,
        30: 0.02,
        40: 0.02,
        110: 0.02,
        120: 0.02,
        121: 0.02,
        122: 0.02,
        130: 0.02,
        150: 0.33,
        151: 0.33,
        152: 0.33,
        153: 0.33,
        180: 0.02,
        190: 0.024,
        200: 0.33,
        201: 0.33,
        202: 0.33,
    },
    "wind": {
        10: 0.15,
        11: 0.15,
        20: 0.15,
        30: 0.15,
        40: 0.15,
        110: 0.15,
        120: 0.15,
        121: 0.15,
        122: 0.15,
        130: 0.15,
        150: 0.33,
        151: 0.33,
        152: 0.33,
        153: 0.33,
        180: 0.15,
        200: 0.33,
        201: 0.33,
        202: 0.33,
    },
}

# ===== Atlite simulation parameters =====
# ERA5 weather data constants
ERA5_DATA_RESOLUTION = 0.25  # degrees
ERA5_DATA_YEAR = 2024
# Coordinates; [max_lat, min_lon, min_lat, max_lon] = [north, west, south, east]
REGION_COORDS = {
    "INDO_AUS": [5.0, 93.0, -50.0, 180.0],
    "AFRICA": [3.0, 7.0, -37.0, 52.0],
    "ALASKA": [72.0, -170.0, 42.0, -50.0],
    "NORTH_AMERICA": [42.0, -128.0, 8.0, -50.0],
    "SOUTH_AMERICA": [14.0, -85.0, -58.0, -33.0],
    "MENA": [38.0, -20.0, 3.0, 62.0],
    "EU": [72.0, -25.0, 35.0, 62.0],
    "NORTH_ASIA": [72.0, 62.0, 50.0, 180.0],
    "SOUTH_ASIA": [50.0, 62.0, 5.0, 148.0],
}
