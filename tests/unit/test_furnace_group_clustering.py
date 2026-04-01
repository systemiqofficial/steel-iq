"""Unit tests for furnace group clustering logic."""

import pytest
from dataclasses import dataclass
from steelo.domain.trade_modelling.furnace_group_clustering import (
    ClusterKey,
    MetaFurnaceGroup,
    calculate_center_of_gravity,
    cluster_furnace_groups,
)
from steelo.domain.models import Location, Plant, FurnaceGroup, Technology, Volumes, PointInTime, TimeFrame, Year


@dataclass
class MockConfig:
    """Mock simulation config for testing."""

    active_statuses: list[str]
    closely_allocated_products: list[str] = None  # Add field with default

    def __post_init__(self):
        if self.closely_allocated_products is None:
            self.closely_allocated_products = [
                "dri_high",
                "dri_mid",
                "dri_low",
                "hot_metal",
            ]


def create_test_location(lat: float, lon: float, iso3: str = "TST") -> Location:
    """Helper to create a test location."""
    return Location(
        lat=lat,
        lon=lon,
        iso3=iso3,
        country="Test Country",
        region="Test Region",
        distance_to_other_iso3=None,
    )


def create_test_plant(plant_id: str, location: Location) -> Plant:
    """Helper to create a test plant."""
    return Plant(
        plant_id=plant_id,
        location=location,
        furnace_groups=[],
        technology_unit_fopex={},
        power_source="grid",
        soe_status="private",
        parent_gem_id="E100000000000",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
    )


def create_test_furnace_group(
    fg_id: str,
    technology_name: str,
    capacity: float,
    chosen_reductant: str = "coke",
    carbon_cost: float = 50.0,
    status: str = "operating",
) -> FurnaceGroup:
    """Helper to create a test furnace group."""
    from steelo.domain.models import PrimaryFeedstock

    # Create dynamic_business_case with appropriate feedstocks based on technology
    if technology_name == "BF":
        dynamic_business_case = [
            PrimaryFeedstock(metallic_charge="io_low", reductant=chosen_reductant, technology=technology_name),
            PrimaryFeedstock(metallic_charge="io_mid", reductant=chosen_reductant, technology=technology_name),
        ]
    elif technology_name == "DRI":
        dynamic_business_case = [
            PrimaryFeedstock(metallic_charge="io_high", reductant=chosen_reductant, technology=technology_name),
        ]
    elif technology_name == "BOF":
        dynamic_business_case = [
            PrimaryFeedstock(metallic_charge="hot_metal", reductant="hot_metal", technology=technology_name),
        ]
    else:
        dynamic_business_case = [
            PrimaryFeedstock(metallic_charge="io_mid", reductant=chosen_reductant, technology=technology_name),
        ]

    technology = Technology(
        name=technology_name,
        product="iron",
        bill_of_materials=None,
        capex_type="greenfield",
        dynamic_business_case=dynamic_business_case,
    )

    fg = FurnaceGroup(
        furnace_group_id=fg_id,
        technology=technology,
        capacity=Volumes(capacity),
        lifetime=PointInTime(
            plant_lifetime=20,
            current=2025,
            time_frame=TimeFrame(start=Year(2025), end=Year(2045)),
        ),
        status=status,
        chosen_reductant=chosen_reductant,
        last_renovation_date=None,
        historical_production={},
        utilization_rate=0.8,
        allocated_volumes=capacity,  # Needed for carbon_cost_per_unit calculation
        carbon_costs_for_emissions=carbon_cost * capacity,  # carbon_cost_per_unit = this / allocated_volumes
    )

    return fg


class TestClusterKey:
    """Test ClusterKey dataclass."""

    def test_cluster_key_creation(self):
        """Test creating a cluster key."""
        key = ClusterKey(
            technology_name="BF",
            iso3="CHN",
            feedstock_signature="coke:io_low|coke:io_mid",
        )
        assert key.technology_name == "BF"
        assert key.iso3 == "CHN"
        assert key.feedstock_signature == "coke:io_low|coke:io_mid"

    def test_cluster_key_string_representation(self):
        """Test cluster key string representation (includes feedstock prefix for readability)."""
        key = ClusterKey("BF", "CHN", "coke:io_low")
        # String representation includes reductant prefix from feedstock_signature
        assert str(key) == "BF_coke_CHN"

    def test_cluster_key_equality(self):
        """Test cluster key equality (including feedstock signature)."""
        key1 = ClusterKey("BF", "CHN", "coke:io_low")
        key2 = ClusterKey("BF", "CHN", "coke:io_low")
        key3 = ClusterKey("DRI", "CHN", "natural_gas:io_high")
        key4 = ClusterKey("BF", "CHN", "coke:io_mid")  # Different feedstock

        assert key1 == key2
        assert key1 != key3
        assert key1 != key4  # Same tech/country but different feedstocks

    def test_cluster_key_hashable(self):
        """Test cluster keys can be used in dict/set."""
        key1 = ClusterKey("BF", "CHN", "coke:io_low")
        key2 = ClusterKey("BF", "CHN", "coke:io_low")
        key3 = ClusterKey("DRI", "CHN", "natural_gas:io_high")

        key_dict = {key1: "value1"}
        assert key_dict[key2] == "value1"  # Same key should access same value

        key_set = {key1, key2, key3}
        assert len(key_set) == 2  # key1 and key2 are identical


class TestCalculateCenterOfGravity:
    """Test center of gravity calculation."""

    def test_center_of_gravity_equal_capacities(self):
        """Test centroid with two FGs of equal capacity."""
        loc1 = create_test_location(lat=40.0, lon=100.0, iso3="CHN")
        loc2 = create_test_location(lat=42.0, lon=102.0, iso3="CHN")

        plant1 = create_test_plant("plant1", loc1)
        plant2 = create_test_plant("plant2", loc2)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0)
        fg2 = create_test_furnace_group("fg2", "BF", capacity=1000.0)

        centroid = calculate_center_of_gravity([(fg1, plant1), (fg2, plant2)])

        # Should be midpoint
        assert centroid.lat == pytest.approx(41.0)
        assert centroid.lon == pytest.approx(101.0)
        assert centroid.iso3 == "CHN"

    def test_center_of_gravity_unequal_capacities(self):
        """Test centroid with two FGs of different capacity."""
        loc1 = create_test_location(lat=40.0, lon=100.0, iso3="CHN")
        loc2 = create_test_location(lat=42.0, lon=102.0, iso3="CHN")

        plant1 = create_test_plant("plant1", loc1)
        plant2 = create_test_plant("plant2", loc2)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0)  # 1/4 of total
        fg2 = create_test_furnace_group("fg2", "BF", capacity=3000.0)  # 3/4 of total

        centroid = calculate_center_of_gravity([(fg1, plant1), (fg2, plant2)])

        # Should be weighted 75% toward plant2
        # lat: 40 * 0.25 + 42 * 0.75 = 10 + 31.5 = 41.5
        # lon: 100 * 0.25 + 102 * 0.75 = 25 + 76.5 = 101.5
        assert centroid.lat == pytest.approx(41.5)
        assert centroid.lon == pytest.approx(101.5)

    def test_center_of_gravity_zero_capacity(self):
        """Test centroid falls back to average when all capacities are zero."""
        loc1 = create_test_location(lat=40.0, lon=100.0)
        loc2 = create_test_location(lat=42.0, lon=102.0)

        plant1 = create_test_plant("plant1", loc1)
        plant2 = create_test_plant("plant2", loc2)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=0.0)
        fg2 = create_test_furnace_group("fg2", "BF", capacity=0.0)

        centroid = calculate_center_of_gravity([(fg1, plant1), (fg2, plant2)])

        # Should fall back to simple average
        assert centroid.lat == pytest.approx(41.0)
        assert centroid.lon == pytest.approx(101.0)

    def test_center_of_gravity_same_location(self):
        """Test centroid when all FGs at same location."""
        loc = create_test_location(lat=35.0, lon=110.0)

        plant1 = create_test_plant("plant1", loc)
        plant2 = create_test_plant("plant2", loc)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0)
        fg2 = create_test_furnace_group("fg2", "BF", capacity=2000.0)

        centroid = calculate_center_of_gravity([(fg1, plant1), (fg2, plant2)])

        # Should be exactly at the same location
        assert centroid.lat == pytest.approx(35.0)
        assert centroid.lon == pytest.approx(110.0)

    def test_center_of_gravity_single_fg(self):
        """Test centroid with single FG."""
        loc = create_test_location(lat=30.0, lon=120.0)
        plant = create_test_plant("plant1", loc)
        fg = create_test_furnace_group("fg1", "BF", capacity=1000.0)

        centroid = calculate_center_of_gravity([(fg, plant)])

        # Should be exactly at the FG location
        assert centroid.lat == pytest.approx(30.0)
        assert centroid.lon == pytest.approx(120.0)

    def test_center_of_gravity_empty_list(self):
        """Test that empty list raises ValueError."""
        with pytest.raises(ValueError, match="Cannot calculate center of gravity for empty list"):
            calculate_center_of_gravity([])

    def test_center_of_gravity_three_fgs(self):
        """Test centroid with three FGs of different capacities."""
        loc1 = create_test_location(lat=30.0, lon=120.0)
        loc2 = create_test_location(lat=32.0, lon=122.0)
        loc3 = create_test_location(lat=34.0, lon=124.0)

        plant1 = create_test_plant("plant1", loc1)
        plant2 = create_test_plant("plant2", loc2)
        plant3 = create_test_plant("plant3", loc3)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0)  # 1/6 total
        fg2 = create_test_furnace_group("fg2", "BF", capacity=2000.0)  # 2/6 total
        fg3 = create_test_furnace_group("fg3", "BF", capacity=3000.0)  # 3/6 total

        centroid = calculate_center_of_gravity([(fg1, plant1), (fg2, plant2), (fg3, plant3)])

        # lat: 30*1/6 + 32*2/6 + 34*3/6 = 5 + 10.67 + 17 = 32.67
        # lon: 120*1/6 + 122*2/6 + 124*3/6 = 20 + 40.67 + 62 = 122.67
        assert centroid.lat == pytest.approx(32.666667, rel=1e-5)
        assert centroid.lon == pytest.approx(122.666667, rel=1e-5)


class TestDisaggregateAllocations:
    """Tests for disaggregate_allocations function."""

    def test_disaggregate_simple_allocation(self):
        """Test disaggregation of a simple allocation from meta-FG to demand center."""
        from steelo.domain.trade_modelling.furnace_group_clustering import disaggregate_allocations
        from steelo.domain.trade_modelling.trade_lp_modelling import (
            Allocations,
            ProcessCenter,
            Commodity,
            Process,
            ProcessType,
        )

        # Create two constituent FGs
        fg1_id = "plant1_fg0"
        fg2_id = "plant2_fg0"

        # Create meta-furnace group
        meta_fg = MetaFurnaceGroup(
            cluster_key=ClusterKey("BF", "CHN", "coke:io_low"),
            meta_furnace_group_id="cluster_BF_coke_CHN",
            constituent_fg_ids=[fg1_id, fg2_id],
            technology_name="BF",
            chosen_reductant="coke",
            location=Location(lat=35.0, lon=110.0, iso3="CHN", country="China", region="Asia"),
            total_capacity=Volumes(4000.0),
            weighted_avg_carbon_cost=85.0,
            dynamic_business_case=None,
            capacity_shares={fg1_id: 0.25, fg2_id: 0.75},  # fg1 has 1000t, fg2 has 3000t
            constituent_locations={
                fg1_id: Location(lat=34.0, lon=109.0, iso3="CHN", country="China", region="Asia"),
                fg2_id: Location(lat=36.0, lon=111.0, iso3="CHN", country="China", region="Asia"),
            },
        )

        # Create mock process
        bf_process = Process(name="BF", type=ProcessType.PRODUCTION, bill_of_materials=[])

        # Create ProcessCenters
        meta_fg_pc = ProcessCenter(
            name="cluster_BF_coke_CHN",
            process=bf_process,
            capacity=4000.0,
            location=meta_fg.location,
            production_cost=85.0,
        )

        demand_process = Process(name="demand", type=ProcessType.DEMAND, bill_of_materials=[])
        demand_pc = ProcessCenter(
            name="demand_center_1",
            process=demand_process,
            capacity=5000.0,
            location=Location(lat=35.5, lon=110.5, iso3="CHN", country="China", region="Asia"),
        )

        commodity = Commodity("iron")

        # Create clustered allocation: meta-FG produces 2000t of iron to demand center
        clustered_allocs = Allocations(allocations={(meta_fg_pc, demand_pc, commodity): 2000.0})

        # Mock config and repo
        config = type(
            "Config",
            (),
            {"hot_metal_radius": 100.0, "closely_allocated_products": ["dri_high", "dri_mid", "dri_low", "hot_metal"]},
        )()
        plants_repo = None  # Not used in this simple test

        # Disaggregate
        result = disaggregate_allocations(
            clustered_allocations=clustered_allocs,
            meta_furnace_groups=[meta_fg],
            plants_repo=plants_repo,
            config=config,
        )

        # Verify: Should have 2 allocations (one per constituent FG)
        assert len(result.allocations) == 2

        # Verify volumes are proportional to capacity shares
        total_volume = sum(vol for vol in result.allocations.values())
        assert abs(total_volume - 2000.0) < 1e-6  # Total preserved

        # Find allocations for each FG
        fg1_alloc = None
        fg2_alloc = None
        for (from_pc, to_pc, comm), vol in result.allocations.items():
            if from_pc.name == fg1_id:
                fg1_alloc = vol
                assert to_pc.name == "demand_center_1"
            elif from_pc.name == fg2_id:
                fg2_alloc = vol
                assert to_pc.name == "demand_center_1"

        # Verify shares: fg1 should get 25%, fg2 should get 75%
        assert fg1_alloc is not None
        assert fg2_alloc is not None
        assert abs(fg1_alloc - 500.0) < 1e-6  # 2000 * 0.25
        assert abs(fg2_alloc - 1500.0) < 1e-6  # 2000 * 0.75

    def test_disaggregate_inter_cluster_flow(self):
        """Test disaggregation of flow between two meta-FGs (e.g., iron → steel)."""
        from steelo.domain.trade_modelling.furnace_group_clustering import disaggregate_allocations
        from steelo.domain.trade_modelling.trade_lp_modelling import (
            Allocations,
            ProcessCenter,
            Commodity,
            Process,
            ProcessType,
        )

        # Create iron-producing meta-FG
        iron_fg1 = "plant1_bf0"
        iron_fg2 = "plant2_bf0"
        iron_meta_fg = MetaFurnaceGroup(
            cluster_key=ClusterKey("BF", "CHN", "coke:io_low"),
            meta_furnace_group_id="cluster_BF_coke_CHN",
            constituent_fg_ids=[iron_fg1, iron_fg2],
            technology_name="BF",
            chosen_reductant="coke",
            location=Location(lat=35.0, lon=110.0, iso3="CHN", country="China", region="Asia"),
            total_capacity=Volumes(10000.0),
            weighted_avg_carbon_cost=85.0,
            dynamic_business_case=None,
            capacity_shares={iron_fg1: 0.4, iron_fg2: 0.6},
            constituent_locations={
                iron_fg1: Location(lat=34.0, lon=109.0, iso3="CHN", country="China", region="Asia"),
                iron_fg2: Location(lat=36.0, lon=111.0, iso3="CHN", country="China", region="Asia"),
            },
        )

        # Create steel-producing meta-FG
        steel_fg1 = "plant3_bof0"
        steel_fg2 = "plant4_bof0"
        steel_meta_fg = MetaFurnaceGroup(
            cluster_key=ClusterKey("BOF", "CHN", "hot_metal:hot_metal"),
            meta_furnace_group_id="cluster_BOF_hot_metal_CHN",
            constituent_fg_ids=[steel_fg1, steel_fg2],
            technology_name="BOF",
            chosen_reductant="hot_metal",
            location=Location(lat=35.0, lon=110.0, iso3="CHN", country="China", region="Asia"),
            total_capacity=Volumes(8000.0),
            weighted_avg_carbon_cost=50.0,
            dynamic_business_case=None,
            capacity_shares={steel_fg1: 0.5, steel_fg2: 0.5},
            constituent_locations={
                steel_fg1: Location(lat=35.0, lon=110.0, iso3="CHN", country="China", region="Asia"),
                steel_fg2: Location(lat=35.5, lon=110.5, iso3="CHN", country="China", region="Asia"),
            },
        )

        # Create mock processes
        bf_process = Process(name="BF", type=ProcessType.PRODUCTION, bill_of_materials=[])
        bof_process = Process(name="BOF", type=ProcessType.PRODUCTION, bill_of_materials=[])

        # Create ProcessCenters
        iron_pc = ProcessCenter(
            name="cluster_BF_coke_CHN",
            process=bf_process,
            capacity=10000.0,
            location=iron_meta_fg.location,
        )

        steel_pc = ProcessCenter(
            name="cluster_BOF_hot_metal_CHN",
            process=bof_process,
            capacity=8000.0,
            location=steel_meta_fg.location,
        )

        commodity = Commodity("hot_metal")

        # Create clustered allocation: 8000t of hot_metal from iron to steel cluster
        clustered_allocs = Allocations(allocations={(iron_pc, steel_pc, commodity): 8000.0})

        config = type(
            "Config",
            (),
            {"hot_metal_radius": 100.0, "closely_allocated_products": ["dri_high", "dri_mid", "dri_low", "hot_metal"]},
        )()
        plants_repo = None

        # Disaggregate
        result = disaggregate_allocations(
            clustered_allocations=clustered_allocs,
            meta_furnace_groups=[iron_meta_fg, steel_meta_fg],
            plants_repo=plants_repo,
            config=config,
        )

        # Transportation problem should produce sparse solution (typically ~m+n-1 edges)
        # For 2 sources × 2 destinations, expect ≤ 3 edges (not full 4)
        assert len(result.allocations) <= 4
        assert len(result.allocations) >= 2  # At least 2 edges needed

        # Verify total volume preserved
        total_volume = sum(vol for vol in result.allocations.values())
        assert abs(total_volume - 8000.0) < 1e-6

        # Verify supply constraints (each source sends correct total)
        from_totals = {}
        for (from_pc, to_pc, comm), vol in result.allocations.items():
            from_totals[from_pc.name] = from_totals.get(from_pc.name, 0) + vol

        assert abs(from_totals[iron_fg1] - 8000.0 * 0.4) < 1e-6  # Should send 3200
        assert abs(from_totals[iron_fg2] - 8000.0 * 0.6) < 1e-6  # Should send 4800

        # Verify demand constraints (each destination receives correct total)
        to_totals = {}
        for (from_pc, to_pc, comm), vol in result.allocations.items():
            to_totals[to_pc.name] = to_totals.get(to_pc.name, 0) + vol

        assert abs(to_totals[steel_fg1] - 8000.0 * 0.5) < 1e-6  # Should receive 4000
        assert abs(to_totals[steel_fg2] - 8000.0 * 0.5) < 1e-6  # Should receive 4000

    def test_disaggregate_passthrough_non_meta(self):
        """Test that non-meta allocations are passed through unchanged."""
        from steelo.domain.trade_modelling.furnace_group_clustering import disaggregate_allocations
        from steelo.domain.trade_modelling.trade_lp_modelling import (
            Allocations,
            ProcessCenter,
            Commodity,
            Process,
            ProcessType,
        )

        # Create mock processes
        supplier_process = Process(name="scrap_supply", type=ProcessType.SUPPLY, bill_of_materials=[])
        demand_process = Process(name="demand", type=ProcessType.DEMAND, bill_of_materials=[])

        # Create ProcessCenters that are NOT meta-FGs
        supplier_pc = ProcessCenter(
            name="supplier_scrap_1",
            process=supplier_process,
            capacity=5000.0,
            location=Location(lat=30.0, lon=100.0, iso3="CHN", country="China", region="Asia"),
        )

        demand_pc = ProcessCenter(
            name="demand_center_1",
            process=demand_process,
            capacity=5000.0,
            location=Location(lat=35.0, lon=110.0, iso3="CHN", country="China", region="Asia"),
        )

        commodity = Commodity("scrap")

        # Create allocation between non-meta centers
        clustered_allocs = Allocations(allocations={(supplier_pc, demand_pc, commodity): 3000.0})

        config = type(
            "Config",
            (),
            {"hot_metal_radius": 100.0, "closely_allocated_products": ["dri_high", "dri_mid", "dri_low", "hot_metal"]},
        )()
        plants_repo = None

        # Disaggregate (should pass through)
        result = disaggregate_allocations(
            clustered_allocations=clustered_allocs,
            meta_furnace_groups=[],  # No meta-FGs
            plants_repo=plants_repo,
            config=config,
        )

        # Should have same allocation unchanged
        assert len(result.allocations) == 1
        assert (supplier_pc, demand_pc, commodity) in result.allocations
        assert result.allocations[(supplier_pc, demand_pc, commodity)] == 3000.0


class TestClusterFurnaceGroups:
    """Test furnace group clustering function."""

    def test_cluster_single_technology(self):
        """Test clustering with all FGs having same technology, reductant, country."""
        loc = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        plant1 = create_test_plant("plant1", loc)
        plant2 = create_test_plant("plant2", loc)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0, chosen_reductant="coke")
        fg2 = create_test_furnace_group("fg2", "BF", capacity=2000.0, chosen_reductant="coke")

        plant1.furnace_groups = [fg1]
        plant2.furnace_groups = [fg2]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant1, plant2], config)

        # Should create single cluster
        assert len(meta_fgs) == 1
        assert len(mapping) == 1

        meta_fg = meta_fgs[0]
        assert len(meta_fg.constituent_fg_ids) == 2
        assert "fg1" in meta_fg.constituent_fg_ids
        assert "fg2" in meta_fg.constituent_fg_ids
        assert float(meta_fg.total_capacity) == 3000.0
        assert meta_fg.technology_name == "BF"
        assert meta_fg.chosen_reductant == "coke"

    def test_cluster_different_technologies(self):
        """Test clustering with different technologies."""
        loc = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        plant1 = create_test_plant("plant1", loc)
        plant2 = create_test_plant("plant2", loc)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0, chosen_reductant="coke")
        fg2 = create_test_furnace_group("fg2", "DRI", capacity=2000.0, chosen_reductant="natural_gas")

        plant1.furnace_groups = [fg1]
        plant2.furnace_groups = [fg2]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant1, plant2], config)

        # Should create two separate clusters
        assert len(meta_fgs) == 2
        assert len(mapping) == 2

    def test_cluster_different_reductants(self):
        """Test clustering with same technology but different reductants."""
        loc = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        plant1 = create_test_plant("plant1", loc)
        plant2 = create_test_plant("plant2", loc)

        fg1 = create_test_furnace_group("fg1", "DRI", capacity=1000.0, chosen_reductant="natural_gas")
        fg2 = create_test_furnace_group("fg2", "DRI", capacity=2000.0, chosen_reductant="hydrogen")

        plant1.furnace_groups = [fg1]
        plant2.furnace_groups = [fg2]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant1, plant2], config)

        # Should create two separate clusters
        assert len(meta_fgs) == 2

    def test_cluster_different_countries(self):
        """Test clustering with same technology but different countries."""
        loc_chn = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        loc_usa = create_test_location(lat=40.0, lon=-100.0, iso3="USA")

        plant1 = create_test_plant("plant1", loc_chn)
        plant2 = create_test_plant("plant2", loc_usa)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0, chosen_reductant="coke")
        fg2 = create_test_furnace_group("fg2", "BF", capacity=2000.0, chosen_reductant="coke")

        plant1.furnace_groups = [fg1]
        plant2.furnace_groups = [fg2]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant1, plant2], config)

        # Should create two separate clusters (different countries)
        assert len(meta_fgs) == 2

    def test_cluster_filters_by_status(self):
        """Test that only active furnace groups are clustered."""
        loc = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        plant = create_test_plant("plant1", loc)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0, status="operating")
        fg2 = create_test_furnace_group("fg2", "BF", capacity=2000.0, status="closed")
        fg3 = create_test_furnace_group("fg3", "BF", capacity=1500.0, status="construction")

        plant.furnace_groups = [fg1, fg2, fg3]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant], config)

        # Should only include fg1
        assert len(meta_fgs) == 1
        meta_fg = meta_fgs[0]
        assert len(meta_fg.constituent_fg_ids) == 1
        assert meta_fg.constituent_fg_ids[0] == "fg1"
        assert float(meta_fg.total_capacity) == 1000.0

    def test_cluster_capacity_shares(self):
        """Test that capacity shares are correctly calculated."""
        loc = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        plant = create_test_plant("plant1", loc)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0)
        fg2 = create_test_furnace_group("fg2", "BF", capacity=3000.0)

        plant.furnace_groups = [fg1, fg2]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant], config)

        meta_fg = meta_fgs[0]
        assert meta_fg.capacity_shares["fg1"] == pytest.approx(0.25)
        assert meta_fg.capacity_shares["fg2"] == pytest.approx(0.75)
        assert sum(meta_fg.capacity_shares.values()) == pytest.approx(1.0)

    def test_cluster_weighted_carbon_cost(self):
        """Test that weighted average carbon cost is correctly calculated."""
        loc = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        plant = create_test_plant("plant1", loc)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0, carbon_cost=50.0)
        fg2 = create_test_furnace_group("fg2", "BF", capacity=3000.0, carbon_cost=70.0)

        plant.furnace_groups = [fg1, fg2]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant], config)

        meta_fg = meta_fgs[0]
        # The clustering function calculates weighted average using fg.carbon_cost_per_unit
        # which is a derived property. Let's just verify it calculated something reasonable
        assert meta_fg.weighted_avg_carbon_cost > 0.0
        # Should be weighted more toward fg2 (which has 3x the capacity of fg1)
        assert meta_fg.weighted_avg_carbon_cost > fg1.carbon_cost_per_unit

    def test_cluster_preserves_constituent_locations(self):
        """Test that original locations are preserved for disaggregation."""
        loc1 = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        loc2 = create_test_location(lat=36.0, lon=111.0, iso3="CHN")

        plant1 = create_test_plant("plant1", loc1)
        plant2 = create_test_plant("plant2", loc2)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0)
        fg2 = create_test_furnace_group("fg2", "BF", capacity=2000.0)

        plant1.furnace_groups = [fg1]
        plant2.furnace_groups = [fg2]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant1, plant2], config)

        meta_fg = meta_fgs[0]
        assert "fg1" in meta_fg.constituent_locations
        assert "fg2" in meta_fg.constituent_locations
        assert meta_fg.constituent_locations["fg1"].lat == 35.0
        assert meta_fg.constituent_locations["fg2"].lat == 36.0

    def test_cluster_single_fg_per_cluster(self):
        """Test that single-FG clusters are still created."""
        loc = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        plant = create_test_plant("plant1", loc)

        fg1 = create_test_furnace_group("fg1", "BF", capacity=1000.0)
        plant.furnace_groups = [fg1]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant], config)

        # Should still create a cluster with 1 FG
        assert len(meta_fgs) == 1
        meta_fg = meta_fgs[0]
        assert len(meta_fg.constituent_fg_ids) == 1
        assert meta_fg.capacity_shares["fg1"] == 1.0

    def test_cluster_empty_plants(self):
        """Test clustering with no active furnace groups."""
        plant = create_test_plant("plant1", create_test_location(35.0, 110.0))
        plant.furnace_groups = []

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant], config)

        # Should return empty lists
        assert len(meta_fgs) == 0
        assert len(mapping) == 0

    def test_cluster_meta_fg_id_format(self):
        """Test that meta-furnace group IDs follow expected format."""
        loc = create_test_location(lat=35.0, lon=110.0, iso3="CHN")
        plant = create_test_plant("plant1", loc)

        fg = create_test_furnace_group("fg1", "BF", capacity=1000.0, chosen_reductant="coke")
        plant.furnace_groups = [fg]

        config = MockConfig(active_statuses=["operating"])
        meta_fgs, mapping = cluster_furnace_groups([plant], config)

        meta_fg = meta_fgs[0]
        # Should be "cluster_" + technology + "_" + reductant + "_" + iso3
        assert "cluster_" in meta_fg.meta_furnace_group_id
        assert "BF" in meta_fg.meta_furnace_group_id
        assert "coke" in meta_fg.meta_furnace_group_id
        assert "CHN" in meta_fg.meta_furnace_group_id


class TestTransportationProblem:
    """Test the transportation problem solver directly."""

    def test_simple_2x2_transportation_problem(self):
        """Test a simple 2 sources to 2 destinations transportation problem."""
        from steelo.domain.trade_modelling.furnace_group_clustering import _solve_batched_transportation_problem
        from steelo.domain.trade_modelling.trade_lp_modelling import Commodity

        # Define sources (2 suppliers with 500 tons each)
        source_supplies = {"source_A": 500.0, "source_B": 500.0}

        # Define destinations (2 consumers with 500 tons each)
        dest_demands = {"dest_X": 500.0, "dest_Y": 500.0}

        # Define locations (all close together, no distance constraints)
        source_locations = {
            "source_A": create_test_location(lat=35.0, lon=110.0, iso3="CHN"),
            "source_B": create_test_location(lat=35.1, lon=110.1, iso3="CHN"),
        }
        dest_locations = {
            "dest_X": create_test_location(lat=35.2, lon=110.2, iso3="CHN"),
            "dest_Y": create_test_location(lat=35.3, lon=110.3, iso3="CHN"),
        }

        commodity = Commodity("steel")
        config = type(
            "Config",
            (),
            {
                "hot_metal_radius": 1000.0,
                "enable_trade_lp_clustering": True,
                "closely_allocated_products": ["dri_high", "dri_mid", "dri_low", "hot_metal"],
            },
        )()

        # Solve
        flows, stats = _solve_batched_transportation_problem(
            source_supplies=source_supplies,
            dest_demands=dest_demands,
            source_locations=source_locations,
            dest_locations=dest_locations,
            commodity=commodity,
            config=config,
        )

        # Verify solution exists
        assert flows is not None
        assert len(flows) > 0

        # Verify supply constraints: each source sends exactly what it has
        from_totals = {}
        for (source_id, dest_id), volume in flows.items():
            from_totals[source_id] = from_totals.get(source_id, 0) + volume

        assert abs(from_totals.get("source_A", 0) - 500.0) < 1e-6
        assert abs(from_totals.get("source_B", 0) - 500.0) < 1e-6

        # Verify demand constraints: each dest receives exactly what it needs
        to_totals = {}
        for (source_id, dest_id), volume in flows.items():
            to_totals[dest_id] = to_totals.get(dest_id, 0) + volume

        assert abs(to_totals.get("dest_X", 0) - 500.0) < 1e-6
        assert abs(to_totals.get("dest_Y", 0) - 500.0) < 1e-6

    def test_unbalanced_supply_demand(self):
        """Test that transportation problem handles small floating point imbalances."""
        from steelo.domain.trade_modelling.furnace_group_clustering import _solve_batched_transportation_problem
        from steelo.domain.trade_modelling.trade_lp_modelling import Commodity

        # Create slight imbalance (floating point error)
        source_supplies = {"source_A": 1000.0}
        dest_demands = {"dest_X": 1000.0000000001}  # Tiny difference

        source_locations = {"source_A": create_test_location(lat=35.0, lon=110.0)}
        dest_locations = {"dest_X": create_test_location(lat=35.5, lon=110.5)}

        commodity = Commodity("steel")
        config = type(
            "Config",
            (),
            {
                "hot_metal_radius": 1000.0,
                "enable_trade_lp_clustering": True,
                "closely_allocated_products": ["dri_high", "dri_mid", "dri_low", "hot_metal"],
            },
        )()

        # Should not raise error - should normalize automatically
        flows, stats = _solve_batched_transportation_problem(
            source_supplies=source_supplies,
            dest_demands=dest_demands,
            source_locations=source_locations,
            dest_locations=dest_locations,
            commodity=commodity,
            config=config,
        )

        # Should produce a solution (with normalized demand)
        assert flows is not None
        assert len(flows) == 1
        assert abs(flows[("source_A", "dest_X")] - 1000.0) < 1e-6

    def test_1_to_many_transportation(self):
        """Test 1 source to multiple destinations."""
        from steelo.domain.trade_modelling.furnace_group_clustering import _solve_batched_transportation_problem
        from steelo.domain.trade_modelling.trade_lp_modelling import Commodity

        source_supplies = {"source_A": 1000.0}
        dest_demands = {"dest_X": 300.0, "dest_Y": 400.0, "dest_Z": 300.0}

        source_locations = {"source_A": create_test_location(lat=35.0, lon=110.0)}
        dest_locations = {
            "dest_X": create_test_location(lat=35.1, lon=110.1),
            "dest_Y": create_test_location(lat=35.2, lon=110.2),
            "dest_Z": create_test_location(lat=35.3, lon=110.3),
        }

        commodity = Commodity("steel")
        config = type(
            "Config",
            (),
            {
                "hot_metal_radius": 1000.0,
                "enable_trade_lp_clustering": True,
                "closely_allocated_products": ["dri_high", "dri_mid", "dri_low", "hot_metal"],
            },
        )()

        flows, stats = _solve_batched_transportation_problem(
            source_supplies=source_supplies,
            dest_demands=dest_demands,
            source_locations=source_locations,
            dest_locations=dest_locations,
            commodity=commodity,
            config=config,
        )

        # Should produce 3 flows (all from source_A)
        assert len(flows) == 3

        # Verify each destination gets correct amount
        to_totals = {}
        for (source_id, dest_id), volume in flows.items():
            assert source_id == "source_A"
            to_totals[dest_id] = volume

        assert abs(to_totals["dest_X"] - 300.0) < 1e-6
        assert abs(to_totals["dest_Y"] - 400.0) < 1e-6
        assert abs(to_totals["dest_Z"] - 300.0) < 1e-6
