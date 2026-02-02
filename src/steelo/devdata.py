"""
Module to create development data for testing and examples. Not used by the model itself.
Allowed to contain hard-coded values.
"""

from datetime import date

from .domain import (
    Plant,
    Location,
    Technology,
    FurnaceGroup,
    ProductCategory,
    Volumes,
    Year,
    DemandCenter,
    Supplier,
)
from .domain.models import PointInTime, TimeFrame

technology_consumption_dict = {
    "BF": {
        "materials": {
            "Iron Ore": {"demand": 2.2, "unit_cost": 1.5},
            "Coke": {"demand": 0.4, "unit_cost": 2.0},
        },
        "energy": {
            "Coal": {"demand": 8.0, "unit_cost": 0.5},
            "Gas": {"demand": 3.0, "unit_cost": 0.5},
        },
    },
    "BOF": {
        "materials": {
            "Iron": {"demand": 0.9, "unit_cost": 2.5},
            "Scrap": {"demand": 0.3, "unit_cost": 3.0},
        },
        "energy": {
            "Electricity": {"demand": 2.0, "unit_cost": 0.75},
        },
    },
    "DRI": {
        "materials": {
            "Iron Ore": {"demand": 1.6, "unit_cost": 1.5},
        },
        "energy": {
            "Gas": {"demand": 12.0, "unit_cost": 0.5},
            "Electricity": {"demand": 2.0, "unit_cost": 0.75},
        },
    },
    "EAF": {
        "materials": {
            "Iron": {"demand": 0.2, "unit_cost": 2.5},
            "Scrap": {"demand": 1.2, "unit_cost": 3.0},
        },
        "energy": {
            "Electricity": {"demand": 6.0, "unit_cost": 0.75},
            "Hydrogen": {"demand": 0.0, "unit_cost": 2.5},
            "Coal": {"demand": 0.0, "unit_cost": 0.5},
        },
    },
}


def get_furnace_group(
    *,
    fg_id: str = "fg_group_1",
    tech_name: str = "EAF",
    production: float = 45000.0,  # Production in tonnes per annum (45 kt)
    utilization_rate: float = 0.7,
    lifetime: PointInTime | None = None,
    capacity: Volumes | None = None,
) -> FurnaceGroup:
    print(
        f"Creating FurnaceGroup with tech_name: {tech_name}, fg_id: {fg_id}, production: {production}, utilization_rate: {utilization_rate}"
    )

    # for testing purposes
    technology_to_product = {
        # Iron technologies
        "BF": "iron",
        "DRI": "iron",
        "ESF": "iron",
        "SR": "iron",
        "BF_Charcoal": "iron",
        "E-WIN": "iron",
        # Steel technologies
        "BOF": "steel",
        "EAF": "steel",
        "MOE": "iron",
    }
    product = technology_to_product.get(tech_name, None)
    if not product:
        raise ValueError(f"Unknown technology name: {tech_name}")
    technology = Technology(
        name=tech_name,
        bill_of_materials=technology_consumption_dict[tech_name],
        product=product,
        dynamic_business_case=[],  # Initialize with empty list for testing
    )
    if not lifetime:
        lifetime = PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2015), end=Year(2035)),
            plant_lifetime=20,
        )
    if not capacity:
        capacity = Volumes(int(production / utilization_rate))
    furnace_group = FurnaceGroup(
        furnace_group_id=fg_id,
        capacity=capacity,
        status="operating",
        last_renovation_date=date(2015, 5, 4),  # TODO @Beth
        technology=technology,
        historical_production={},
        utilization_rate=utilization_rate,
        lifetime=lifetime,
    )

    # Set the bill_of_materials so unit_vopex can be calculated
    furnace_group.bill_of_materials = technology_consumption_dict[tech_name]

    return furnace_group


def get_plant(
    *,
    plant_id: str = "plant_1",
    tech_name: str = "EAF",
    unit_production_cost: float = 70.0,
    production: float = 45000.0,  # Production in tonnes per annum (45 kt)
    location: Location | None = None,
    furnace_groups: list[FurnaceGroup] | None = None,
) -> Plant:
    if not location:
        location = Location(iso3="DEU", country="Germany", region="Europe", lat=49.40768, lon=8.69079)
    if not furnace_groups:
        furnace_groups = [
            get_furnace_group(
                fg_id=f"{plant_id}_fg",
                tech_name=tech_name,
                production=production,
            )
        ]
    # Get default technology_fopex for the location
    from .domain.fopex import regional_fopex

    technology_fopex = {}
    if location.iso3 in regional_fopex:
        region_data = regional_fopex[location.iso3]
        technology_fopex = {k.lower(): v for k, v in region_data.items() if isinstance(v, (int, float))}

    return Plant(
        plant_id=plant_id,
        location=location,
        furnace_groups=furnace_groups,
        power_source="unknown",
        soe_status="unknown",
        parent_gem_id="gem_id",
        workforce_size=10,
        certified=False,  # TODO @Beth
        category_steel_product={ProductCategory("Flat")},
        technology_unit_fopex=technology_fopex,
    )


def get_demand_center(
    *,
    centre_id: str = "demand_center_1",
    gravity: Location | None = None,
    demand: dict[Year, Volumes] | None = None,
) -> DemandCenter:
    if not demand:
        demand = {Year(2025): Volumes(10), Year(2026): Volumes(20)}
    if not gravity:
        gravity = Location(lat=0.0, lon=0.0, country="", region="", iso3="")
    return DemandCenter(
        demand_center_id=centre_id,
        center_of_gravity=gravity,
        demand_by_year=demand,
    )


furnace_announcement_dates = [
    ("announced", 2028),
    ("Operating", 2024),
    ("Operating", 2024),
    ("announced", 2030),
    ("Operating", 2024),
    ("announced", 2031),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("announced", 2035),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("announced", 2030),
    ("Operating", 2024),
    ("announced", 2032),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("announced", 2031),
    ("construction", 2034),
    ("announced", 2032),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("announced", 2030),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("announced", 2031),
    ("Operating", 2024),
    ("Operating", 2024),
    ("announced", 2032),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
    ("Operating", 2024),
]


bom_mock = {
    "materials": {
        "Burnt dolomite": {"demand": 0.008615554298366596, "unit_cost": {"Value": 50.0, "Unit": "USD/t"}},
        "Burnt lime": {"demand": 0.024985107465263126, "unit_cost": {"Value": 50.0, "Unit": "USD/t"}},
        "Hot metal": {"demand": 1.095838046722067, "unit_cost": {"Value": 361.36931660975426, "Unit": "USD/t"}},
    },
    "energy": {"Electricity": {"demand": 34.85817316437359, "unit_cost": {"Value": 0.235, "Unit": "USD/kWh"}}},
}


def get_test_suppliers() -> list[Supplier]:
    """Create a comprehensive set of suppliers for testing."""
    suppliers = []

    # Scrap suppliers
    suppliers.append(
        Supplier(
            supplier_id="scrap_supplier_ger",
            location=Location(lat=52.5200, lon=13.4050, country="Germany", region="Europe", iso3="DEU"),
            commodity="scrap",
            capacity_by_year={Year(2025): Volumes(2000), Year(2026): Volumes(2000), Year(2027): Volumes(2000)},
            production_cost_by_year={Year(2025): 300.0, Year(2026): 300.0, Year(2027): 300.0},
            mine_cost_by_year={},
            mine_price_by_year={},
        )
    )

    suppliers.append(
        Supplier(
            supplier_id="scrap_supplier_fra",
            location=Location(lat=48.8566, lon=2.3522, country="France", region="Europe", iso3="FRA"),
            commodity="scrap",
            capacity_by_year={Year(2025): Volumes(1500), Year(2026): Volumes(1500), Year(2027): Volumes(1500)},
            production_cost_by_year={Year(2025): 320.0, Year(2026): 320.0, Year(2027): 320.0},
            mine_cost_by_year={},
            mine_price_by_year={},
        )
    )

    # Iron ore suppliers
    suppliers.append(
        Supplier(
            supplier_id="io_high_supplier_aus",
            location=Location(lat=-20.0, lon=118.0, country="Australia", region="Oceania", iso3="AUS"),
            commodity="io_high",
            capacity_by_year={Year(2025): Volumes(5000), Year(2026): Volumes(5000), Year(2027): Volumes(5000)},
            production_cost_by_year={Year(2025): 50.0, Year(2026): 50.0, Year(2027): 50.0},
            mine_cost_by_year={Year(2025): 50.0, Year(2026): 50.0, Year(2027): 50.0},
            mine_price_by_year={Year(2025): 60.0, Year(2026): 60.0, Year(2027): 60.0},
        )
    )

    suppliers.append(
        Supplier(
            supplier_id="io_mid_supplier_bra",
            location=Location(lat=-15.0, lon=-47.0, country="Brazil", region="South America", iso3="BRA"),
            commodity="io_mid",
            capacity_by_year={Year(2025): Volumes(4000), Year(2026): Volumes(4000), Year(2027): Volumes(4000)},
            production_cost_by_year={Year(2025): 60.0, Year(2026): 60.0, Year(2027): 60.0},
            mine_cost_by_year={Year(2025): 60.0, Year(2026): 60.0, Year(2027): 60.0},
            mine_price_by_year={Year(2025): 70.0, Year(2026): 70.0, Year(2027): 70.0},
        )
    )

    suppliers.append(
        Supplier(
            supplier_id="io_low_supplier_ind",
            location=Location(lat=20.0, lon=77.0, country="India", region="Asia", iso3="IND"),
            commodity="io_low",
            capacity_by_year={Year(2025): Volumes(3000), Year(2026): Volumes(3000), Year(2027): Volumes(3000)},
            production_cost_by_year={Year(2025): 40.0, Year(2026): 40.0, Year(2027): 40.0},
            mine_cost_by_year={Year(2025): 40.0, Year(2026): 40.0, Year(2027): 40.0},
            mine_price_by_year={Year(2025): 50.0, Year(2026): 50.0, Year(2027): 50.0},
        )
    )

    # Pellets suppliers
    suppliers.append(
        Supplier(
            supplier_id="pellets_high_supplier_swe",
            location=Location(lat=60.0, lon=18.0, country="Sweden", region="Europe", iso3="SWE"),
            commodity="pellets_high",
            capacity_by_year={Year(2025): Volumes(2000), Year(2026): Volumes(2000), Year(2027): Volumes(2000)},
            production_cost_by_year={Year(2025): 80.0, Year(2026): 80.0, Year(2027): 80.0},
            mine_cost_by_year={Year(2025): 80.0, Year(2026): 80.0, Year(2027): 80.0},
            mine_price_by_year={Year(2025): 90.0, Year(2026): 90.0, Year(2027): 90.0},
        )
    )

    suppliers.append(
        Supplier(
            supplier_id="pellets_mid_supplier_can",
            location=Location(lat=45.0, lon=-75.0, country="Canada", region="North America", iso3="CAN"),
            commodity="pellets_mid",
            capacity_by_year={Year(2025): Volumes(1800), Year(2026): Volumes(1800), Year(2027): Volumes(1800)},
            production_cost_by_year={Year(2025): 75.0, Year(2026): 75.0, Year(2027): 75.0},
            mine_cost_by_year={Year(2025): 75.0, Year(2026): 75.0, Year(2027): 75.0},
            mine_price_by_year={Year(2025): 85.0, Year(2026): 85.0, Year(2027): 85.0},
        )
    )

    # Coal and coke suppliers
    suppliers.append(
        Supplier(
            supplier_id="coal_supplier_usa",
            location=Location(lat=40.0, lon=-100.0, country="USA", region="North America", iso3="USA"),
            commodity="scrap",  # Using SCRAP as placeholder for coal
            capacity_by_year={Year(2025): Volumes(10000), Year(2026): Volumes(10000), Year(2027): Volumes(10000)},
            production_cost_by_year={Year(2025): 100.0, Year(2026): 100.0, Year(2027): 100.0},
            mine_cost_by_year={},
            mine_price_by_year={},
        )
    )

    suppliers.append(
        Supplier(
            supplier_id="coke_supplier_ger",
            location=Location(lat=51.0, lon=7.0, country="Germany", region="Europe", iso3="DEU"),
            commodity="scrap",  # Using SCRAP as placeholder for coke
            capacity_by_year={Year(2025): Volumes(3000), Year(2026): Volumes(3000), Year(2027): Volumes(3000)},
            production_cost_by_year={Year(2025): 200.0, Year(2026): 200.0, Year(2027): 200.0},
            mine_cost_by_year={},
            mine_price_by_year={},
        )
    )

    # Note: "Prep Sinter" should be a production process, not a supply process
    # It will be created from a furnace group with technology name "Prep Sinter"

    return suppliers


def get_test_demand_centers() -> list[DemandCenter]:
    """Create comprehensive demand centers for testing."""
    return [
        DemandCenter(
            demand_center_id="europe_dc",
            center_of_gravity=Location(lat=50.0, lon=10.0, country="Germany", region="Europe", iso3="DEU"),
            demand_by_year={Year(2025): Volumes(100), Year(2026): Volumes(110), Year(2027): Volumes(120)},
        ),
        DemandCenter(
            demand_center_id="asia_dc",
            center_of_gravity=Location(lat=35.0, lon=105.0, country="China", region="Asia", iso3="CHN"),
            demand_by_year={Year(2025): Volumes(80), Year(2026): Volumes(85), Year(2027): Volumes(90)},
        ),
    ]
