"""TRQ gateway-node helpers for the trade LP.

This module turns active TariffRateQuota objects into gateway process-center nodes
that are injected into the LP before it is built.  The gateway approach models tiered
tariffs as a network of pass-through nodes:

    plant → gateway_tier_0 (capacity = tariff_free_quota, cost = in-quota rate × price)
    plant → gateway_tier_1 (capacity = ∞,                cost = OOQ rate × price)
    gateway_tier_k → demand_center (cost = avg transport from from_iso3s)

The in-quota tariff rate defaults to 0 (duty-free), so by default the tier-0 arc cost is 0.
Because the LP minimises total cost, it fills the cheaper (in-quota) gateway first
without any explicit ordering constraints.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from steelo.domain.models import (
    TRQ_STEEL_TYPE_ANY,
    TRQ_STEEL_TYPE_CONVENTIONAL,
    TRQ_STEEL_TYPE_GREEN,
)

if TYPE_CHECKING:
    from steelo.domain.models import TariffRateQuota
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp

logger = logging.getLogger(__name__)

# Sentinel ISO3 assigned to every gateway node so it never matches a real tariff key.
_GATEWAY_ISO3 = "__GWY__"

# Fallback capacity used for the "unlimited" final tier (large but finite, avoids
# Pyomo warnings about unbounded variables in capacity constraints).
_UNLIMITED_CAPACITY = 1e9


@dataclass
class TRQGatewayNode:
    """Represents one tariff tier of one TRQ as a gateway node in the LP.

    Attributes:
        node_id: Unique string identifier used as the LP process-center name.
        trq_name: Name of the parent TariffRateQuota.
        tier_index: 0-based tier index (0 = duty-free, 1 = first OOQ tier, …).
        conventional_tariff_rate: Ad-valorem tariff for conventional steel, 0–100 scale.
        green_tariff_rate: Ad-valorem tariff for green steel, 0–100 scale. The rate a plant
            actually pays is selected by its green-steel eligibility (see compute_gateway_arc_costs).
        tier_capacity: Volume cap in tonnes (None treated as _UNLIMITED_CAPACITY).
        from_iso3s: Eligible exporting ISO3 codes.
        to_iso3s: Eligible importing ISO3 codes.
        commodity: Commodity name (e.g. "steel").
        shared_quota_id: Shared pool identifier, or None.
        steel_type: Which steel this TRQ applies to (TRQ_STEEL_TYPE_ANY / _CONVENTIONAL / _GREEN).
            Determines which plants (by green-steel eligibility) may flow through this gateway.
            See TariffRateQuota.
    """

    node_id: str
    trq_name: str
    tier_index: int
    conventional_tariff_rate: float
    green_tariff_rate: float
    tier_capacity: float | None
    from_iso3s: list[str]
    to_iso3s: list[str]
    commodity: str
    shared_quota_id: str | None = None
    steel_type: str = TRQ_STEEL_TYPE_ANY

    @property
    def effective_capacity(self) -> float:
        return self.tier_capacity if self.tier_capacity is not None else _UNLIMITED_CAPACITY


def trq_steel_type_applies_to_plant(steel_type: str, plant_green_eligible: bool) -> bool:
    """Return True if a plant is subject to a TRQ restricted to `steel_type`.

    - TRQ_STEEL_TYPE_ANY: all steel is subject.
    - TRQ_STEEL_TYPE_GREEN: only green-steel-eligible plants are subject.
    - TRQ_STEEL_TYPE_CONVENTIONAL: only non-green (conventional) plants are subject.

    Plants not subject to the TRQ are fully exempt: they bypass the gateway and keep their
    direct plant→demand-centre arc, so they are never counted against the quota.
    """
    if steel_type == TRQ_STEEL_TYPE_GREEN:
        return plant_green_eligible
    if steel_type == TRQ_STEEL_TYPE_CONVENTIONAL:
        return not plant_green_eligible
    return True  # TRQ_STEEL_TYPE_ANY (or unrecognised) → applies to all plants


def build_trq_gateway_nodes(active_trqs: list[TariffRateQuota]) -> list[TRQGatewayNode]:
    """Convert a list of active TariffRateQuota objects into TRQGatewayNode objects.

    Each TRQ produces one gateway node per tier (typically two: duty-free + OOQ).
    Shared-quota TRQs (same shared_quota_id) must already be merged into a single
    TariffRateQuota with a combined from_iso3s list before calling this function.

    Args:
        active_trqs: TRQs active in the current simulation year.

    Returns:
        Flat list of TRQGatewayNode objects, one per (TRQ, tier) combination.
    """
    nodes: list[TRQGatewayNode] = []
    for trq in active_trqs:
        for tier_index, tier in enumerate(trq.tiers):
            quota_suffix = trq.shared_quota_id or "_".join(trq.from_iso3s[:3])
            node_id = f"TRQ_{trq.name}_{quota_suffix}_tier_{tier_index}".replace(" ", "_")
            nodes.append(
                TRQGatewayNode(
                    node_id=node_id,
                    trq_name=trq.name,
                    tier_index=tier_index,
                    conventional_tariff_rate=tier.conventional_tariff_rate,
                    green_tariff_rate=tier.green_tariff_rate,
                    tier_capacity=tier.capacity,
                    from_iso3s=list(trq.from_iso3s),
                    to_iso3s=list(trq.to_iso3s),
                    commodity=trq.commodity,
                    shared_quota_id=trq.shared_quota_id,
                    steel_type=trq.steel_type,
                )
            )
    logger.info(f"Built {len(nodes)} TRQ gateway nodes from {len(active_trqs)} active TRQs")
    return nodes


def create_gateway_process_centers(
    gateway_nodes: list[TRQGatewayNode],
) -> list[tlp.ProcessCenter]:
    """Create LP ProcessCenter objects for each gateway node.

    Gateway process centers have:
    - ProcessType.GATEWAY so the LP knows to skip BOM/ratio constraints.
    - A dummy location (ISO3 = _GATEWAY_ISO3) so they never match real tariff lookups.
    - capacity = gateway.effective_capacity (enforced as outbound-flow cap).
    - production_cost = 0.

    Args:
        gateway_nodes: Nodes produced by build_trq_gateway_nodes().

    Returns:
        List of ProcessCenter objects ready to be added to a TradeLPModel.
    """
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp

    pcs: list[tlp.ProcessCenter] = []
    gateway_process = tlp.Process(
        name="__gateway__",
        type=tlp.ProcessType.GATEWAY,
        bill_of_materials=[],
    )
    dummy_location = tlp.Location(
        lat=0.0,
        lon=0.0,
        country="gateway",
        iso3=_GATEWAY_ISO3,
        region="gateway",
    )
    for gw in gateway_nodes:
        pc = tlp.ProcessCenter(
            name=gw.node_id,
            process=gateway_process,
            capacity=gw.effective_capacity,
            location=dummy_location,
            production_cost=0.0,
        )
        pcs.append(pc)
    return pcs


def gateway_to_country_transport_cost(
    gateway: TRQGatewayNode,
    to_iso3: str,
    transport_lookup: dict[tuple[str, str, str], float],
) -> float:
    """Compute the average transport cost from a gateway's exporting countries to a destination.

    Uses equal weighting across all from_iso3s that have data.  Countries with no
    transport data for the route contribute zero to the average (with a debug log).

    Args:
        gateway: The gateway node whose from_iso3s are the possible origins.
        to_iso3: Destination country ISO3.
        transport_lookup: Dict keyed by (from_iso3, to_iso3, commodity) → cost per tonne.

    Returns:
        Average transport cost (0.0 when no data is found for any origin).
    """
    costs: list[float] = []
    commodity = gateway.commodity
    for from_iso3 in gateway.from_iso3s:
        cost = transport_lookup.get((from_iso3, to_iso3, commodity), None)
        if cost is not None:
            costs.append(cost)
        else:
            logger.debug(f"No transport data for ({from_iso3} → {to_iso3}, {commodity}); excluded from gateway average")
    if not costs:
        logger.warning(
            f"Gateway {gateway.node_id}: no transport data found for any origin → {to_iso3} ({commodity}). Using 0."
        )
        return 0.0
    return sum(costs) / len(costs)


def compute_gateway_arc_costs(
    gateway_nodes: list[TRQGatewayNode],
    process_centers: list[tlp.ProcessCenter],
    average_commodity_price_per_region: dict[tuple[str, str], float],
    transport_lookup: dict[tuple[str, str, str], float],
) -> dict[tuple[str, str, str], float]:
    """Pre-compute allocation costs for all gateway arcs.

    Returns a dict keyed by (from_pc_name, to_pc_name, commodity_name) → cost per tonne.

    Plant → gateway arcs: tariff cost only (tariff_rate / 100 × avg commodity price at origin).
    Gateway → demand-center arcs: demand-weighted average transport cost.

    Args:
        gateway_nodes: Gateway nodes from build_trq_gateway_nodes().
        process_centers: All process centers already in the LP model (plants + DCs).
        average_commodity_price_per_region: From env.average_commodity_price_per_region,
            keyed by (commodity, iso3) → average price USD/t.
        transport_lookup: Dict keyed by (from_iso3, to_iso3, commodity) → cost per tonne.

    Returns:
        Dict of pre-computed costs for gateway arcs.
    """
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp

    costs: dict[tuple[str, str, str], float] = {}

    # Build fast lookups from process centers
    production_pcs_by_iso3: dict[str, list[tlp.ProcessCenter]] = {}
    demand_pcs_by_iso3: dict[str, list[tlp.ProcessCenter]] = {}
    for pc in process_centers:
        iso3 = pc.location.iso3
        if iso3 == _GATEWAY_ISO3:
            continue
        if pc.process.type == tlp.ProcessType.PRODUCTION:
            production_pcs_by_iso3.setdefault(iso3, []).append(pc)
        elif pc.process.type == tlp.ProcessType.DEMAND:
            demand_pcs_by_iso3.setdefault(iso3, []).append(pc)

    for gw in gateway_nodes:
        commodity = gw.commodity

        # --- Plant → gateway arcs: tariff cost ---
        for from_iso3 in gw.from_iso3s:
            # Each plant pays the conventional or green rate depending on its green-steel
            # eligibility. Precompute both costs per origin; pick per plant below.
            price = average_commodity_price_per_region.get((commodity, from_iso3), 0.0)
            if (gw.conventional_tariff_rate > 0 or gw.green_tariff_rate > 0) and price == 0.0:
                logger.warning(
                    f"No average price for ({commodity}, {from_iso3}); tariff cost for gateway {gw.node_id} set to 0."
                )
            conventional_cost = (gw.conventional_tariff_rate / 100.0) * price
            green_cost = (gw.green_tariff_rate / 100.0) * price
            for plant_pc in production_pcs_by_iso3.get(from_iso3, []):
                # Steel-type scope: a plant only flows through this gateway if the TRQ's steel_type
                # applies to it (green plant ↔ green TRQ, conventional plant ↔ conventional TRQ,
                # any TRQ ↔ both). Non-subject plants are fully exempt — no gateway arc is created,
                # so they keep their direct plant→DC arc and are never counted against the quota.
                if not trq_steel_type_applies_to_plant(gw.steel_type, plant_pc.green_steel_eligible):
                    continue
                # Green-eligible plants pay the green rate; conventional plants the conventional
                # rate. Under a single-type TRQ only the matching plants reach here, so only that
                # type's rate is ever used.
                tariff_cost = green_cost if plant_pc.green_steel_eligible else conventional_cost
                costs[(plant_pc.name, gw.node_id, commodity)] = tariff_cost

        # --- Gateway → demand-center arcs: transport cost ---
        for to_iso3 in gw.to_iso3s:
            transport_cost = gateway_to_country_transport_cost(gw, to_iso3, transport_lookup)
            for dc_pc in demand_pcs_by_iso3.get(to_iso3, []):
                costs[(gw.node_id, dc_pc.name, commodity)] = transport_cost

    logger.info(f"Pre-computed {len(costs)} gateway arc costs")
    return costs


def collect_trq_covered_routes(
    gateway_nodes: list[TRQGatewayNode],
) -> dict[tuple[str, str, str], set[str]]:
    """Map each (from_iso3, to_iso3, commodity) route to the steel-type scopes covering it.

    Only duty-free (tier 0) gateways are used to identify covered routes — if a route is
    governed by a TRQ it is covered regardless of which tier steel ends up flowing through.

    Because coverage now depends on steel type, a route may map to several scopes (e.g. a
    conventional-steel TRQ and a green-steel TRQ on the same lane). A direct plant→DC arc is
    only removed if at least one covering scope applies to that specific plant — see
    trq_steel_type_applies_to_plant and TradeLPModel._inject_trq_gateway_arcs.

    Args:
        gateway_nodes: All gateway nodes for the current year.

    Returns:
        Dict mapping (from_iso3, to_iso3, commodity) → set of steel-type scopes covering it.
    """
    covered: dict[tuple[str, str, str], set[str]] = {}
    # Use tier-0 nodes as the canonical representative of each TRQ
    for gw in gateway_nodes:
        if gw.tier_index == 0:
            for from_iso3 in gw.from_iso3s:
                for to_iso3 in gw.to_iso3s:
                    covered.setdefault((from_iso3, to_iso3, gw.commodity), set()).add(gw.steel_type)
    return covered
