# ===== Physical Constants =====
GRAVITY_ACCELERATION = 9.81  # m/s^2
EARTH_RADIUS_KM = 6371.0  # mean radius, for great-circle (haversine) distances
DAYS_IN_YEAR = 365
HOURS_IN_DAY = 24
HOURS_IN_YEAR = DAYS_IN_YEAR * HOURS_IN_DAY

# ===== External data grids =====
ESA_CCI_CELLS_PER_DEG = 360  # ESA-CCI land-cover 300 m grid

# ===== Unit Conversions =====
KILO_TO_MEGA = 1000  # kW -> MW, kWh -> MWh

# ===== Battery sizing =====
AVERAGE_IMPLIED_STORAGE = 4  # h/GWh of battery capacity; modular battery CAPEX correction

# ===== Numerical Constants =====
EPSILON = 1e-10  # small epsilon to avoid division by zero

# ===== Output schema =====
# Per-pixel feasibility status (int8 `status` variable in optimal-solution NetCDFs, band 12 of optimal-solution COG)
# Lets map tell the four "no result" cases apart instead of rendering them all as basemap-white
STATUS_CODES = {
    0: "Not modelled",
    1: "Optimum found",
    2: "Simulated, no usable optimum",
    3: "Zero solar and wind potential",
    4: "Fragile optimum rejected by minimum-survivor cut",
}
