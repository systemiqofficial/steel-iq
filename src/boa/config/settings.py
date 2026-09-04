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

# Scale of the grid-bisection search box, as a multiple of 1/CF: `search_box` (bisection.py)
# sets `mu = OVERSCALE_SAMPLING_K[tech] / CF_tech` (per-pixel time-mean capacity factor) and
# spans the box to `box_multiple * mu`, so the search tracks the site's resource. The
# capacity ceiling is applied downstream, never inside the search box. Search-tuning knob,
# not a physical parameter.
OVERSCALE_SAMPLING_K = {"wind": 0.75, "solar": 0.75}

# Default spacing (years) for re-anchoring a pixel's frontier against updated costs
# across a multi-decade horizon, rather than one frontier built once serving every query
# year off a single frozen anchor. Chosen from BOA_BISECTION_PLAN.md's re-anchoring
# benchmark: the excess-LCOE tail from an out-of-date anchor stayed in a flat ~1.2-1.4%
# band out to a 20-year-old anchor and only jumped sharply at 25 years, so 10-year
# spacing (worst-case drift 5 years) already captures most of the benefit a much more
# frequent re-anchor would buy, at roughly half the extra build cost. See
# `boa.model.bisection.anchor_years`/`nearest_anchor` -- building and routing to more
# than one frontier per pixel is not implemented yet (BOA_BISECTION_PLAN.md's M2).
REANCHOR_INTERVAL_YEARS = 10

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
