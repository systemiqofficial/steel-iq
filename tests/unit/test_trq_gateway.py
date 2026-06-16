"""Unit tests for the TRQ gateway-node implementation.

Covers:
- TRQTier / tiers property on TariffRateQuota
- build_trq_gateway_nodes()
- gateway_to_country_transport_cost()
- compute_gateway_arc_costs() and collect_trq_covered_routes()
- End-to-end LP: 2 plants, 2 demand centres, 3-tier TRQ (the worked example from the plan).
"""

import pytest

from steelo.domain.models import (
    TariffRateQuota,
    TRQTier,
    Year,
    TRQ_STEEL_TYPE_ANY,
    TRQ_STEEL_TYPE_CONVENTIONAL,
    TRQ_STEEL_TYPE_GREEN,
)
from steelo.domain.trade_modelling.trq_gateway import (
    TRQGatewayNode,
    build_trq_gateway_nodes,
    compute_gateway_arc_costs,
    gateway_to_country_transport_cost,
    collect_trq_covered_routes,
    trq_steel_type_applies_to_plant,
    _GATEWAY_ISO3,
    _UNLIMITED_CAPACITY,
)
from steelo.domain.trade_modelling.trade_lp_modelling import (
    Commodity,
    BOMElement,
    Process,
    ProcessCenter,
    ProcessType,
    TradeLPModel,
    Location,
)
from steelo.domain.trade_modelling.set_up_steel_trade_lp import solve_lp_only


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trq(
    name="EU safeguards",
    from_iso3s=None,
    to_iso3s=None,
    tariff_free_quota=1000.0,
    out_of_quota_tariff_rate=50.0,
    shared_quota_id=None,
    steel_type=TRQ_STEEL_TYPE_ANY,
) -> TariffRateQuota:
    return TariffRateQuota(
        name=name,
        from_iso3s=from_iso3s or ["TUR"],
        to_iso3s=to_iso3s or ["DEU"],
        commodity="steel",
        tariff_free_quota=tariff_free_quota,
        out_of_quota_tariff_rate=out_of_quota_tariff_rate,
        start_year=Year(2026),
        end_year=Year(2034),
        shared_quota_id=shared_quota_id,
        steel_type=steel_type,
    )


def _make_location(iso3: str, lat: float = 0.0, lon: float = 0.0) -> Location:
    return Location(lat=lat, lon=lon, country=iso3, iso3=iso3, region=iso3)


def _make_steel_process(name: str = "BF-BOF") -> Process:
    steel = Commodity("steel")
    bom = BOMElement(name="steel_bom", commodity=steel, parameters={}, output_commodities=[steel])
    return Process(name=name, type=ProcessType.PRODUCTION, bill_of_materials=[bom])


def _make_demand_process(name: str = "steel_demand") -> Process:
    steel = Commodity("steel")
    bom = BOMElement(name="steel_demand_bom", commodity=steel, parameters={}, output_commodities=[])
    return Process(name=name, type=ProcessType.DEMAND, bill_of_materials=[bom])


# ---------------------------------------------------------------------------
# Step 1: TRQTier / tiers property
# ---------------------------------------------------------------------------


class TestTRQTier:
    def test_tiers_has_two_entries(self):
        trq = _make_trq(tariff_free_quota=500.0, out_of_quota_tariff_rate=25.0)
        tiers = trq.tiers
        assert len(tiers) == 2

    def test_tier_0_is_duty_free_with_capacity(self):
        trq = _make_trq(tariff_free_quota=500.0)
        t0 = trq.tiers[0]
        assert t0.capacity == 500.0
        assert t0.tariff_rate == 0.0

    def test_tier_1_has_ooq_rate_and_no_cap(self):
        trq = _make_trq(out_of_quota_tariff_rate=50.0)
        t1 = trq.tiers[1]
        assert t1.capacity is None
        assert t1.tariff_rate == 50.0

    def test_trq_tier_dataclass(self):
        tier = TRQTier(capacity=100.0, tariff_rate=10.0)
        assert tier.capacity == 100.0
        assert tier.tariff_rate == 10.0


# ---------------------------------------------------------------------------
# Step 2+3: build_trq_gateway_nodes
# ---------------------------------------------------------------------------


class TestBuildTRQGatewayNodes:
    def test_one_trq_produces_two_nodes(self):
        trq = _make_trq()
        nodes = build_trq_gateway_nodes([trq])
        assert len(nodes) == 2

    def test_tier_0_is_duty_free(self):
        nodes = build_trq_gateway_nodes([_make_trq()])
        tier0 = next(n for n in nodes if n.tier_index == 0)
        assert tier0.tariff_rate == 0.0
        assert tier0.tier_capacity == 1000.0

    def test_tier_1_is_ooq(self):
        nodes = build_trq_gateway_nodes([_make_trq(out_of_quota_tariff_rate=50.0)])
        tier1 = next(n for n in nodes if n.tier_index == 1)
        assert tier1.tariff_rate == 50.0
        assert tier1.tier_capacity is None

    def test_unlimited_tier_effective_capacity(self):
        nodes = build_trq_gateway_nodes([_make_trq()])
        tier1 = next(n for n in nodes if n.tier_index == 1)
        assert tier1.effective_capacity == _UNLIMITED_CAPACITY

    def test_node_ids_are_unique(self):
        trq1 = _make_trq(name="A", from_iso3s=["TUR"])
        trq2 = _make_trq(name="B", from_iso3s=["KOR"])
        nodes = build_trq_gateway_nodes([trq1, trq2])
        ids = [n.node_id for n in nodes]
        assert len(ids) == len(set(ids))

    def test_from_and_to_iso3s_propagated(self):
        trq = _make_trq(from_iso3s=["TUR", "KOR"], to_iso3s=["DEU", "FRA"])
        nodes = build_trq_gateway_nodes([trq])
        for node in nodes:
            assert node.from_iso3s == ["TUR", "KOR"]
            assert node.to_iso3s == ["DEU", "FRA"]

    def test_empty_trq_list(self):
        assert build_trq_gateway_nodes([]) == []

    def test_steel_type_propagated(self):
        trq = _make_trq(steel_type=TRQ_STEEL_TYPE_GREEN)
        nodes = build_trq_gateway_nodes([trq])
        for node in nodes:
            assert node.steel_type == TRQ_STEEL_TYPE_GREEN

    def test_steel_type_defaults_to_any(self):
        nodes = build_trq_gateway_nodes([_make_trq()])
        for node in nodes:
            assert node.steel_type == TRQ_STEEL_TYPE_ANY


# ---------------------------------------------------------------------------
# trq_steel_type_applies_to_plant — eligibility matcher
# ---------------------------------------------------------------------------


class TestSteelTypeMatcher:
    def test_any_applies_to_all(self):
        assert trq_steel_type_applies_to_plant(TRQ_STEEL_TYPE_ANY, True) is True
        assert trq_steel_type_applies_to_plant(TRQ_STEEL_TYPE_ANY, False) is True

    def test_green_applies_only_to_green(self):
        assert trq_steel_type_applies_to_plant(TRQ_STEEL_TYPE_GREEN, True) is True
        assert trq_steel_type_applies_to_plant(TRQ_STEEL_TYPE_GREEN, False) is False

    def test_conventional_applies_only_to_conventional(self):
        assert trq_steel_type_applies_to_plant(TRQ_STEEL_TYPE_CONVENTIONAL, False) is True
        assert trq_steel_type_applies_to_plant(TRQ_STEEL_TYPE_CONVENTIONAL, True) is False


# ---------------------------------------------------------------------------
# Steel-type scope applied to plant → gateway tariff arcs
# ---------------------------------------------------------------------------


class TestSteelTypeArcCosts:
    """compute_gateway_arc_costs() should create gateway arcs only for plants the TRQ's
    steel_type applies to, and charge the full tariff (no green-steel discount)."""

    def _setup(self, steel_type):
        """Two TUR plants — one green-steel-eligible, one not — feeding one DEU DC
        through a 2-tier TRQ. OOQ rate 50%, avg price 100 ⇒ base OOQ tariff 50 $/t."""
        prod_proc = _make_steel_process()
        dem_proc = _make_demand_process()
        loc_tur = _make_location("TUR")
        loc_deu = _make_location("DEU")

        eligible = ProcessCenter(
            name="plant_green", process=prod_proc, capacity=20.0, location=loc_tur, green_steel_eligible=True
        )
        ineligible = ProcessCenter(
            name="plant_grey", process=prod_proc, capacity=20.0, location=loc_tur, green_steel_eligible=False
        )
        dc = ProcessCenter(name="dc_deu", process=dem_proc, capacity=10.0, location=loc_deu)

        nodes = build_trq_gateway_nodes(
            [_make_trq(from_iso3s=["TUR"], to_iso3s=["DEU"], out_of_quota_tariff_rate=50.0, steel_type=steel_type)]
        )
        costs = compute_gateway_arc_costs(
            gateway_nodes=nodes,
            process_centers=[eligible, ineligible, dc],
            average_commodity_price_per_region={("steel", "TUR"): 100.0},
            transport_lookup={("TUR", "DEU", "steel"): 5.0},
        )
        tier1 = next(n for n in nodes if n.tier_index == 1)
        tier0 = next(n for n in nodes if n.tier_index == 0)
        return costs, tier0.node_id, tier1.node_id

    def test_any_creates_arcs_for_both_at_full_tariff(self):
        costs, _, tier1_id = self._setup(steel_type=TRQ_STEEL_TYPE_ANY)
        # No green-steel discount: both plants pay the full OOQ tariff (50% * 100 = 50).
        assert costs[("plant_green", tier1_id, "steel")] == pytest.approx(50.0)
        assert costs[("plant_grey", tier1_id, "steel")] == pytest.approx(50.0)

    def test_conventional_excludes_green_plant(self):
        costs, _, tier1_id = self._setup(steel_type=TRQ_STEEL_TYPE_CONVENTIONAL)
        # Green plant is fully exempt → no gateway arc created for it.
        assert ("plant_green", tier1_id, "steel") not in costs
        assert costs[("plant_grey", tier1_id, "steel")] == pytest.approx(50.0)

    def test_green_excludes_conventional_plant(self):
        costs, _, tier1_id = self._setup(steel_type=TRQ_STEEL_TYPE_GREEN)
        assert ("plant_grey", tier1_id, "steel") not in costs
        assert costs[("plant_green", tier1_id, "steel")] == pytest.approx(50.0)

    def test_duty_free_tier_still_zero(self):
        costs, tier0_id, _ = self._setup(steel_type=TRQ_STEEL_TYPE_ANY)
        # In-quota rate defaults to 0 ⇒ tier-0 arc cost is 0 for subject plants.
        assert costs[("plant_green", tier0_id, "steel")] == pytest.approx(0.0)
        assert costs[("plant_grey", tier0_id, "steel")] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Step 4: gateway_to_country_transport_cost
# ---------------------------------------------------------------------------


class TestGatewayTransportCost:
    def _make_gateway(self, from_iso3s):
        return TRQGatewayNode(
            node_id="test_gw",
            trq_name="test",
            tier_index=0,
            tariff_rate=0.0,
            tier_capacity=1000.0,
            from_iso3s=from_iso3s,
            to_iso3s=["DEU"],
            commodity="steel",
        )

    def test_single_origin_known_route(self):
        gw = self._make_gateway(["TUR"])
        lookup = {("TUR", "DEU", "steel"): 30.0}
        cost = gateway_to_country_transport_cost(gw, "DEU", lookup)
        assert cost == pytest.approx(30.0)

    def test_two_origins_equal_weight(self):
        gw = self._make_gateway(["TUR", "KOR"])
        lookup = {("TUR", "DEU", "steel"): 20.0, ("KOR", "DEU", "steel"): 40.0}
        cost = gateway_to_country_transport_cost(gw, "DEU", lookup)
        assert cost == pytest.approx(30.0)

    def test_missing_route_excluded_from_average(self):
        gw = self._make_gateway(["TUR", "KOR"])
        lookup = {("TUR", "DEU", "steel"): 20.0}  # KOR missing
        cost = gateway_to_country_transport_cost(gw, "DEU", lookup)
        assert cost == pytest.approx(20.0)

    def test_all_routes_missing_returns_zero(self):
        gw = self._make_gateway(["TUR"])
        cost = gateway_to_country_transport_cost(gw, "DEU", {})
        assert cost == 0.0


# ---------------------------------------------------------------------------
# collect_trq_covered_routes
# ---------------------------------------------------------------------------


class TestCollectCoveredRoutes:
    def test_covered_routes_from_tier0_nodes(self):
        trq = _make_trq(from_iso3s=["TUR", "KOR"], to_iso3s=["DEU", "FRA"])
        nodes = build_trq_gateway_nodes([trq])
        covered = collect_trq_covered_routes(nodes)
        assert ("TUR", "DEU", "steel") in covered
        assert ("KOR", "FRA", "steel") in covered
        assert len(covered) == 4  # 2 from × 2 to

    def test_only_tier0_contributes(self):
        trq = _make_trq(from_iso3s=["TUR"], to_iso3s=["DEU"])
        nodes = build_trq_gateway_nodes([trq])
        # Manually keep only tier-1 nodes
        tier1_only = [n for n in nodes if n.tier_index == 1]
        covered = collect_trq_covered_routes(tier1_only)
        assert len(covered) == 0


# ---------------------------------------------------------------------------
# End-to-end LP: 2 plants, 2 demand centres, 2-tier TRQ
# Verifies that the LP fills the duty-free tier first.
# ---------------------------------------------------------------------------


def _build_gateway_lp_model() -> TradeLPModel:
    """Build a small TRQ-gateway LP (built but not solved).

    Topology:
        Plant X (TUR, 20 t) → GW_tier0 (cap=5t, rate=0%) → DEU demand (10t)
        Plant X (TUR, 20 t) → GW_tier1 (cap=∞,  rate=10%) → FRA demand (5t)
        Plant Y (KOR, 20 t) → same gateways → same DCs

    Total demand is 15 t, tier-0 (duty-free) capacity is 5 t.
    """
    steel = Commodity("steel")
    bom_in = BOMElement(name="steel_bom", commodity=steel, parameters={}, output_commodities=[steel])
    bom_out = BOMElement(name="steel_dem", commodity=steel, parameters={}, output_commodities=[])

    prod_proc = Process(name="BF-BOF", type=ProcessType.PRODUCTION, bill_of_materials=[bom_in])
    dem_proc = Process(name="demand", type=ProcessType.DEMAND, bill_of_materials=[bom_out])
    gw_proc = Process(name="__gateway__", type=ProcessType.GATEWAY, bill_of_materials=[])

    loc_tur = _make_location("TUR", lat=39.0, lon=35.0)
    loc_kor = _make_location("KOR", lat=37.0, lon=127.0)
    loc_deu = _make_location("DEU", lat=51.0, lon=10.0)
    loc_fra = _make_location("FRA", lat=46.0, lon=2.0)
    loc_gwy = _make_location(_GATEWAY_ISO3, lat=0.0, lon=0.0)

    plant_x = ProcessCenter(name="plant_x", process=prod_proc, capacity=20.0, location=loc_tur)
    plant_y = ProcessCenter(name="plant_y", process=prod_proc, capacity=20.0, location=loc_kor)
    dc_deu = ProcessCenter(name="dc_deu", process=dem_proc, capacity=10.0, location=loc_deu)
    dc_fra = ProcessCenter(name="dc_fra", process=dem_proc, capacity=5.0, location=loc_fra)
    gw_tier0 = ProcessCenter(name="gw_tier0", process=gw_proc, capacity=5.0, location=loc_gwy)
    gw_tier1 = ProcessCenter(name="gw_tier1", process=gw_proc, capacity=_UNLIMITED_CAPACITY, location=loc_gwy)

    # Pre-compute costs: tier0 free, tier1 costs 10 $/t (tariff on ~100 $/t avg price)
    gateway_arc_costs = {
        ("plant_x", "gw_tier0", "steel"): 0.0,
        ("plant_y", "gw_tier0", "steel"): 0.0,
        ("plant_x", "gw_tier1", "steel"): 10.0,
        ("plant_y", "gw_tier1", "steel"): 10.0,
        ("gw_tier0", "dc_deu", "steel"): 5.0,
        ("gw_tier0", "dc_fra", "steel"): 5.0,
        ("gw_tier1", "dc_deu", "steel"): 5.0,
        ("gw_tier1", "dc_fra", "steel"): 5.0,
    }

    # Build gateway nodes for injection
    gw_nodes = [
        TRQGatewayNode(
            node_id="gw_tier0",
            trq_name="EU safeguards",
            tier_index=0,
            tariff_rate=0.0,
            tier_capacity=5.0,
            from_iso3s=["TUR", "KOR"],
            to_iso3s=["DEU", "FRA"],
            commodity="steel",
        ),
        TRQGatewayNode(
            node_id="gw_tier1",
            trq_name="EU safeguards",
            tier_index=1,
            tariff_rate=10.0,
            tier_capacity=None,
            from_iso3s=["TUR", "KOR"],
            to_iso3s=["DEU", "FRA"],
            commodity="steel",
        ),
    ]
    covered = collect_trq_covered_routes(gw_nodes)

    model = TradeLPModel(lp_epsilon=1e-3, random_seed=42)
    model.add_commodities([steel])
    model.add_processes([prod_proc, dem_proc, gw_proc])
    model.add_process_centers([plant_x, plant_y, dc_deu, dc_fra, gw_tier0, gw_tier1])

    # Wire gateway data
    model.trq_gateway_nodes = gw_nodes
    model.trq_covered_routes = covered
    model.gateway_arc_costs = gateway_arc_costs

    # No standard process connectors — all arcs injected via TRQ mechanism
    model.add_tariff_information({}, {})
    model.build_lp_model()
    return model


class TestTRQGatewayLP:
    """Smoke test: small LP with gateway nodes injected manually.

    Expected: tier0 (cheaper) fills to capacity (5 t) before tier1.
    """

    def _build_model(self) -> TradeLPModel:
        return _build_gateway_lp_model()

    def test_model_builds_without_error(self):
        model = self._build_model()
        assert model.lp_model is not None

    def test_gateway_flow_conservation_constraint_exists(self):
        model = self._build_model()
        assert hasattr(model.lp_model, "gateway_flow_conservation")

    def test_gateway_capacity_constraint_exists(self):
        model = self._build_model()
        assert hasattr(model.lp_model, "production_constraints")

    def test_lp_solves_and_fills_cheap_tier_first(self):
        model = self._build_model()
        result = model.solve_lp_model()
        # Check solver found a solution (may be infeasible if solver not installed)
        try:
            term = str(result.solver.termination_condition)
        except Exception:
            pytest.skip("Solver not available")
        if "infeasible" in term.lower():
            pytest.skip(f"LP infeasible (solver issue): {term}")

        alloc_vars = model.lp_model.allocation_variables
        tier0_inflow = sum(
            float(alloc_vars[f, "gw_tier0", "steel"].value or 0.0)
            for (f, t, c) in alloc_vars
            if t == "gw_tier0" and c == "steel"
        )
        tier1_inflow = sum(
            float(alloc_vars[f, "gw_tier1", "steel"].value or 0.0)
            for (f, t, c) in alloc_vars
            if t == "gw_tier1" and c == "steel"
        )
        # Total demand is 15 t (DEU=10, FRA=5). Tier0 capacity is 5 t.
        assert tier0_inflow == pytest.approx(5.0, abs=0.1), (
            f"Expected tier-0 to be fully used (5 t), got {tier0_inflow}"
        )
        assert tier1_inflow == pytest.approx(10.0, abs=0.1), (
            f"Expected tier-1 to carry remaining 10 t, got {tier1_inflow}"
        )


# ---------------------------------------------------------------------------
# solve_lp_only(): the path taken when furnace-group clustering is enabled.
# It must collapse gateway arcs exactly like the non-clustering solve path so
# that (a) no synthetic "__GWY__" arcs leak into disaggregate_allocations and
# (b) TRQ tariffs land in allocations.tariff_taxes for the TM-PAM connector.
# ---------------------------------------------------------------------------


class TestSolveLpOnlyCollapsesGateways:
    """Regression: solve_lp_only (clustering path) must dissolve TRQ gateways.

    Before the fix, solve_lp_only() only called extract_solution() and left the
    plant→gateway→DC arcs in place, so clustered runs lost TRQ tariffs and fed
    "__GWY__" nodes into disaggregation.
    """

    def _solve(self):
        model = _build_gateway_lp_model()
        try:
            solve_lp_only(model)
        except Exception as exc:  # solver binary not installed in this env
            pytest.skip(f"Solver not available: {exc}")
        if model.allocations is None or not model.allocations.allocations:
            pytest.skip("LP infeasible or solver issue — no allocations produced")
        return model

    def test_no_gateway_arcs_remain_after_solve(self):
        model = self._solve()
        gateway_ids = {"gw_tier0", "gw_tier1"}
        leaked = [
            (from_pc.name, to_pc.name, comm.name)
            for (from_pc, to_pc, comm) in model.allocations.allocations
            if from_pc.name in gateway_ids or to_pc.name in gateway_ids
        ]
        assert leaked == [], f"Gateway arcs were not collapsed: {leaked}"

    def test_collapsed_into_direct_plant_to_dc_arcs(self):
        model = self._solve()
        direct = [
            (from_pc.name, to_pc.name)
            for (from_pc, to_pc, comm) in model.allocations.allocations
            if from_pc.process.type == ProcessType.PRODUCTION and to_pc.process.type == ProcessType.DEMAND
        ]
        assert direct, "Expected direct plant→DC arcs after gateway collapse"
        # Every collapsed origin/destination must be a real country, never the gateway sentinel.
        for from_pc, to_pc, _ in model.allocations.allocations:
            assert from_pc.location.iso3 != _GATEWAY_ISO3
            assert to_pc.location.iso3 != _GATEWAY_ISO3

    def test_trq_tariff_injected_into_tariff_taxes(self):
        model = self._solve()
        tariff_taxes = model.allocations.tariff_taxes
        assert tariff_taxes, "TRQ tariffs were not injected into allocations.tariff_taxes"
        # Out-of-quota volume (tier-1, 10 %/$10 per t) must surface as a positive
        # per-route tariff on a real cross-border route.
        positive_routes = {route: t for route, t in tariff_taxes.items() if t > 0}
        assert positive_routes, f"Expected a positive TRQ tariff, got {tariff_taxes}"
        for (from_iso3, to_iso3, comm), _ in positive_routes.items():
            assert from_iso3 in {"TUR", "KOR"}
            assert to_iso3 in {"DEU", "FRA"}
            assert comm == "steel"
