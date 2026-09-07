"""
Exogenous, real-world inputs: technology lifetimes, degradation and cost-scaling factors,
land-cover densities, weather/geography constants. Every value here is either cited to a
source or explicitly flagged as a team ballpark -- none of it is a search-tuning knob.

Tunable algorithm parameters (search box sizing, grid resolution, battery rungs, anchor
tolerance) live in `boa.model.bisection.SearchParams` instead, not here -- that dataclass is
hashed into the frontier cache path, so it is the single place a change there is guaranteed
to force a rebuild rather than silently reuse an incompatible store.
"""

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

# Deterioration rate of the energy systems over their lifetime. Not read anywhere in the LCOE
# calculation today -- lcoe_coefficients and calculate_lcoe_of_re_installation_vectorised
# (cost_calculations.py) have no deterioration term, live or dead. Kept as a sourced input
# for if/when that term gets wired in, rather than re-deriving the numbers from scratch.
YEARLY_DETERIORATION_RATES = {
    "solar": 0.005,  # 0.5%/year
    "wind": 0.01,  # 1%/year
    "battery": 0.015,  # 1.5%/year; batteries degrade faster (NREL, see README.md)
}

# ===== Max-capacity ceiling parameters (boa_cds max-capacity) =====
# Applied density = theoretical density (stage 1) x packing factor (stage 2) x land-
# availability fraction (stage 3, LULC_CODES below). Full source trail, the min-vs-multiply
# reasoning, and per-class rationale: BOA_BISECTION_PLAN.md, "LULC_CODES rewrite".

# Stage 1: zero-spacing areal power density. Scholz (2012) REMix PhD thesis, Tab. 4.1.3 (pv)
# / 4.3.1 (wind) -- https://elib.dlr.de/77976/1/REMix_Thesis_YS.pdf
THEORETICAL_DENSITY_MW_PER_KM2 = {"pv": 141.9, "wind": 10.42}

# Stage 2: fraction of a site's own footprint actually covered (row spacing, access roads).
# pv: Scholz Tab. 4.1.3, cross-validated to ~20-40% by 5 independent sources (Ong et al.
# 2013 NREL/TP-6A20-56290, NREL/TP-6A20-87843 2023, Risch et al. 2022 doi:10.3390/en15155536).
# wind: 1.0 -- turbine self-spacing is already inside the stage-1 figure.
PACKING_FACTOR = {"pv": 0.33, "wind": 1.0}

# Overridden per-run via --pv-density / --wind-density.
CAPACITY_DENSITY_MW_PER_KM2 = {
    tech: THEORETICAL_DENSITY_MW_PER_KM2[tech] * PACKING_FACTOR[tech] for tech in ("pv", "wind")
}

# Stage 3: ESA-CCI LCCS class -> land-availability fraction. Unlisted classes get 0.
#
# !!! BALLPARKED, NOT LITERATURE-DERIVED. RECHECK BEFORE TRUSTING A RUN. !!!
#
# No source gave a fraction confirmed safe to multiply against stage 2 without double-
# counting (Scholz's own figures turned out to be Germany-specific fallow-farmland stats,
# not a transferable constant). These are a team judgement call instead of a citation.
LULC_CODES = {
    "pv": {
        10: 0.10,
        11: 0.10,
        12: 0.10,  # cropland, rainfed (+ herbaceous / tree-shrub cover)
        20: 0.10,  # cropland, irrigated or post-flooding
        30: 0.10,
        40: 0.10,  # cropland / natural-vegetation mosaics
        100: 0.10,
        110: 0.10,  # tree/shrub <-> herbaceous mosaics
        120: 0.10,
        121: 0.10,
        122: 0.10,  # shrubland
        130: 0.10,  # grassland
        140: 1.0,  # lichens and mosses (bare/sparse bracket)
        150: 1.0,
        151: 1.0,
        152: 1.0,
        153: 1.0,  # sparse vegetation
        190: 0.20,  # urban -- rooftop proxy
        200: 1.0,
        201: 1.0,
        202: 1.0,  # bare areas
        # forest (50-90), wetland (160/170/180), water (210), snow/ice (220): excluded
    },
    "wind": {
        10: 0.10,
        11: 0.10,
        12: 0.10,
        20: 0.10,
        30: 0.10,
        40: 0.10,  # cropland (+ mosaics)
        50: 0.10,
        60: 0.10,
        61: 0.10,
        62: 0.10,
        70: 0.10,
        71: 0.10,
        72: 0.10,
        80: 0.10,
        81: 0.10,
        82: 0.10,
        90: 0.10,  # forest, all types -- WDPA-protected forest
        # is already excluded upstream by cds_exclusion; this covers the non-protected rest
        100: 0.10,
        110: 0.10,
        120: 0.10,
        121: 0.10,
        122: 0.10,
        130: 0.10,
        140: 1.0,
        150: 1.0,
        151: 1.0,
        152: 1.0,
        153: 1.0,  # lichens/mosses, sparse vegetation
        200: 1.0,
        201: 1.0,
        202: 1.0,  # bare areas
        # urban (190), wetland (160/170/180), water (210), snow/ice (220): excluded
    },
}

# ===== Copernicus data parameters =====
# ERA5 weather data constants
ERA5_DATA_RESOLUTION = 0.25  # degrees
ERA5_DATA_YEAR = 2024
# Coordinates; [max_lat, min_lon, min_lat, max_lon] = [north, west, south, east]
#
# Deliberately excludes Antarctica (lat < -60) and the high Arctic (lat > 72, the ceiling
# every region caps out at) -- no plant siting candidate there. Adjacent boxes are offset by
# one grid cell (ERA5_DATA_RESOLUTION) rather than touching exactly, so no land cell is
# double-built/double-queried. HAWAII, GALAPAGOS, MASCARENE and KERGUELEN are small dedicated
# boxes for inhabited islands that fall in the longitude gap between two continental boxes --
# cheaper than stretching a continental box across an ocean to reach them. The four largest
# boxes are each split into a point-balanced west/east pair on a real geographic line, since
# build-cache only persists a region once every one of its points has finished (no incremental
# write within a region) -- a smaller box bounds how much work a mid-build crash can lose.
REGION_COORDS = {
    "INDO_AUS": [5.0, 93.0, -50.0, 180.0],
    "SUB_SAHARAN_AFRICA": [3.0, 7.0, -37.0, 52.0],
    "ALASKA": [72.0, -170.0, 42.0, -110.25],
    "CANADA": [72.0, -110.0, 42.0, -50.0],
    "NORTH_AMERICA": [41.75, -128.0, 8.0, -50.0],
    "SOUTH_AMERICA": [7.75, -85.0, -58.0, -33.0],
    "NORTH_AFRICA": [34.75, -20.0, 3.25, 19.75],
    "MIDDLE_EAST": [34.75, 20.0, 3.25, 62.0],
    "EUROPE": [72.0, -25.0, 35.0, 62.0],
    "NORTH_ASIA_WEST": [72.0, 62.25, 50.0, 109.75],
    "NORTH_ASIA_EAST": [72.0, 110.0, 50.0, 180.0],
    "SOUTH_ASIA": [49.75, 62.25, 5.25, 94.75],
    "EAST_ASIA": [49.75, 95.0, 5.25, 151.0],
    "HAWAII": [22.5, -160.0, 18.75, -154.5],
    "GALAPAGOS": [0.25, -92.0, -1.5, -89.5],
    "MASCARENE": [-19.75, 55.0, -21.75, 58.25],
    "KERGUELEN": [-48.75, 68.5, -50.0, 70.75],
}
