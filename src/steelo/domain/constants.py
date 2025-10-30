from typing import NewType, Sequence
from enum import Enum
import numpy as np

Volumes = NewType("Volumes", float)  # Volumes of steel or iron (unit: ttpa)
Year = NewType("Year", int)
Years = Sequence[Year]

# ===== Physical Constants =====
GRAVITY_ACCELERATION = 9.81  # m/s^2
EARTH_RADIUS = 6371.0088  # km

# ===== Numerical Constants =====
LP_EPSILON = 1e-3  # Linear programming solver epsilon
LP_TOLERANCE = 1e-4  # Linear programming solver tolerance, values below are treated as zero

# ===== Hardcoded Parameters =====
MINIMUM_UTILIZATION_RATE_FOR_COST_CURVE = 0.3
MINIMUM_PRODUCTION_VOLUME_FOR_COST_CURVE = 50e3  # tpa; minimum production volume for a plant to have a cost curve

# ===== Unit Conversion Factors =====
GJ_TO_KWH = 1e3 / 3.6  # 1 GJ = 1e3/3e6 kWh and 1/3.6 MWh
MWH_TO_KWH = 1e3
TWH_TO_KWH = 1e9
GJ_TO_KG_HYDROGEN = 1 / 0.144
PERMWh_TO_PERkWh = 1 / MWH_TO_KWH
PERkWh_TO_PERMWh = 1 / PERMWh_TO_PERkWh
PERGJ_TO_PERkWh = 1 / GJ_TO_KWH  # Energy price (per GJ) → per kWh
KG_TO_T = 1e-3  # kilograms to tonnes
T_TO_KG = 1 / KG_TO_T  # tonnes to kilograms (1000 kg/t) - used for price unit conversion USD/kg → USD/t
KT_TO_T = 1e3
T_TO_KT = 1 / KT_TO_T
MT_TO_T = 1e6  # 1 million tonnes
T_TO_MT = 1 / MT_TO_T
MioUSD_TO_USD = 1e6
BioUSD_TO_USD = 1e9
USD_TO_MioUSD = 1 / MioUSD_TO_USD
USD_TO_BioUSD = 1 / BioUSD_TO_USD
rad_TO_deg = 180 / np.pi  # radians to degrees

# ===== Other Constants =====
GEO_RESOLUTION = 0.25  # degrees; to change the resolution of the geospatial analysis, the baseload
# power optimization must be interpolated - currently not implemented, hence set as constant.
PLANT_LIFETIME = 20  # years; default lifetime of a plant (used for data preparation only, since
# called before the SimulationConfig is created. For the actual simulation, the lifetime in the
# SimulationConfig is used. The data preparation lifetime is only used to determine the renovation
# cycle position of existing plants.
MIN_CAPACITY_FOR_DISTANCE_CALCULATION = 1 * MT_TO_T  # t; minimum capacity for plants to be considered
# in the distance to closest location calculation in the geospatial analysis. This is to speed up the
# calculation - locations with a capacity below this threshold are ignored.


# ===== Fixed Enumerations =====
class Commodities(Enum):
    """Types of commodities in the steel production process."""

    STEEL = "steel"
    IRON = "iron"
    HOT_METAL = "hot_metal"
    DRI_LOW = "dri_low"
    DRI_MID = "dri_mid"
    DRI_HIGH = "dri_high"
    HBI_LOW = "hbi_low"
    HBI_MID = "hbi_mid"
    HBI_HIGH = "hbi_high"
    PIG_IRON = "pig_iron"
    LIQUID_STEEL = "liquid_steel"
    IO_LOW = "io_low"
    IO_MID = "io_mid"
    IO_HIGH = "io_high"
    SCRAP = "scrap"
    ELECTROLYTIC_IRON = "electrolytic_iron"
    LIQUID_IRON = "liquid_iron"


# Product categories - moved to SimulationConfig
CLOSELY_ALLOCATED_PRODUCTS = [
    Commodities.DRI_HIGH.value,
    Commodities.DRI_MID.value,
    Commodities.DRI_LOW.value,
    Commodities.HOT_METAL.value,
    Commodities.LIQUID_IRON.value,
]
DISTANTLY_ALLOCATED_PRODUCTS = [
    Commodities.HBI_HIGH.value,
    Commodities.HBI_MID.value,
    Commodities.HBI_LOW.value,
    Commodities.PIG_IRON.value,
    Commodities.ELECTROLYTIC_IRON.value,
]
IRON_PRODUCTS = ["iron", "hot_metal", "pig_iron", "dri_low", "dri_mid", "dri_high", "hbi_low", "hbi_mid", "hbi_high"]
# Note: CLOSElY_ALLOCATED_PRODUCTS with lowercase 'l' is an alias for CLOSELY_ALLOCATED_PRODUCTS
CLOSElY_ALLOCATED_PRODUCTS = CLOSELY_ALLOCATED_PRODUCTS


# Sub-country demand and scrap share per center for major steel consuming countries
# Share in % of the country's totals
# TODO: Place in master excel? Or document very clearly at least.
MAJOR_DEMAND_AND_SUPPLY_CENTERS = {
    "USA_California": {"iso3": "USA", "latitude": 38, "longitude": -122, "share": 0.34},
    "USA_NorthEast": {"iso3": "USA", "latitude": 41, "longitude": -75, "share": 0.22},
    "USA_Texas": {"iso3": "USA", "latitude": 30, "longitude": -94, "share": 0.22},
    "USA_Midwest": {"iso3": "USA", "latitude": 41, "longitude": -87, "share": 0.22},
    "Canada_East": {"iso3": "CAN", "latitude": 45, "longitude": -76, "share": 0.77},
    "Canada_West": {"iso3": "CAN", "latitude": 50, "longitude": -122, "share": 0.23},
    "China_Hebei_Shandong": {"iso3": "CHN", "latitude": 38, "longitude": 117, "share": 0.37},
    "China_Guandong": {"iso3": "CHN", "latitude": 23, "longitude": 114, "share": 0.25},
    "China_Hubei_Henan": {"iso3": "CHN", "latitude": 32, "longitude": 114, "share": 0.19},
    "China_Sichuan": {"iso3": "CHN", "latitude": 30, "longitude": 104, "share": 0.19},
    "Australia_West": {"iso3": "AUS", "latitude": -32, "longitude": 116, "share": 0.43},
    "Australia_East": {"iso3": "AUS", "latitude": -34, "longitude": 150, "share": 0.57},
    "Brazil_South": {"iso3": "BRA", "latitude": -23, "longitude": -45, "share": 0.83},
    "Brazil_North": {"iso3": "BRA", "latitude": -2, "longitude": -48, "share": 0.17},
}
