from collections import defaultdict

import pyomo.environ as pyo

from steelo.domain.models import Location
from steelo.domain.trade_modelling import trade_lp_modelling as tlp


def _make_bof_process_center(name="bof_pc"):
    process = tlp.Process(name="BOF", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])
    location = Location(lat=0.0, lon=0.0, country="dummy", region="dummy", iso3="XXX")
    return tlp.ProcessCenter(name=name, process=process, capacity=100.0, location=location)


def test_add_aggregate_commodity_constraints_matches_case_insensitive_mask():
    """Excel-sourced masks are uppercase (e.g. "DRI"); LP commodity names are always
    lowercase (Commodity.__init__ lowercases). The matcher must match despite this,
    using the same case-insensitive prefix semantics as matches_feedstock."""
    trade_lp = tlp.TradeLPModel()
    pc = _make_bof_process_center()
    trade_lp.process_centers = [pc]
    trade_lp.aggregated_commodity_constraints = {("BOF", "DRI"): {"maximum": 0.15}}

    arcs = [
        ("dri_supplier", pc.name, "dri_high"),
        ("hbi_supplier", pc.name, "hbi_low"),
        ("scrap_supplier", pc.name, "scrap"),
    ]
    trade_lp.lp_model.inbound_arcs = defaultdict(list, {pc.name: arcs})
    trade_lp.lp_model.allocation_variables = pyo.Var(arcs)
    for idx in arcs:
        trade_lp.lp_model.allocation_variables[idx].fix(0)

    trade_lp.add_aggregate_commodity_constraint_parameters()
    trade_lp.add_aggregate_commodity_constraints_to_lp()

    assert trade_lp.lp_model.allocations_of_bom_commodity_agg[(pc.name, "DRI")] == {
        ("dri_supplier", pc.name, "dri_high")
    }
    assert trade_lp.lp_model.all_inbound_allocations_agg[(pc.name, "DRI")] == set(arcs)

    # The constraint must actually be built (not silently skipped) now that the mask matches
    assert (pc.name, "DRI") in trade_lp.lp_model.aggregate_commodity_maximum_ratio_constraints
    constraint = trade_lp.lp_model.aggregate_commodity_maximum_ratio_constraints[pc.name, "DRI"]
    assert constraint.body is not None


def test_add_bom_energy_costs_prefers_process_center_override():
    """Two ProcessCenters sharing one technology-wide Process (built from whichever furnace
    group came first) must each get their own energy cost, not the shared BOM's baked-in
    value, whenever a facility-specific override is present."""
    scrap = tlp.Commodity(name="scrap")
    steel = tlp.Commodity(name="steel")
    bom_element = tlp.BOMElement(
        name="scrap_feed",
        commodity=scrap,
        output_commodities=[steel],
        parameters={},
        energy_cost=10.0,  # baked in from whichever furnace group built this shared Process
    )
    process = tlp.Process(name="EAF", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[bom_element])
    location = Location(lat=0.0, lon=0.0, country="dummy", region="dummy", iso3="XXX")

    pc_no_override = tlp.ProcessCenter(name="fg_no_override", process=process, capacity=50.0, location=location)
    pc_with_override = tlp.ProcessCenter(
        name="fg_with_override",
        process=process,
        capacity=80.0,
        location=location,
        energy_costs_per_input={"scrap": 40.0},
    )

    trade_lp = tlp.TradeLPModel()
    trade_lp.process_centers = [pc_no_override, pc_with_override]

    trade_lp.add_bom_energy_costs_as_parameter_to_lp()

    # No override: falls back to the shared BOM's baked-in energy_cost
    assert trade_lp.lp_model.bom_energy_costs[("fg_no_override", "scrap")] == 10.0
    # Override present: facility-specific cost wins over the shared BOM's value
    assert trade_lp.lp_model.bom_energy_costs[("fg_with_override", "scrap")] == 40.0


def test_set_legal_allocations_does_not_collapse_reductant_variant_processes():
    """Two Process objects sharing a technology (e.g. "BF") but built for different
    reductants each carry their own BOM. set_legal_allocations pre-indexes primary/dependent
    commodities in a dict keyed by the Process object itself — since Process.__eq__/__hash__
    are name-based, distinct-name variants must NOT collapse into one entry, and both must
    still be able to legally allocate into a shared downstream technology (BOF) wired via
    per-variant ProcessConnectors (as the fixed connector-wiring loop now produces)."""
    location = Location(lat=0.0, lon=0.0, country="dummy", region="dummy", iso3="XXX")
    iron_ore = tlp.Commodity(name="iron_ore")
    hot_metal = tlp.Commodity(name="hot_metal")
    steel = tlp.Commodity(name="steel")

    bof_bom = tlp.BOMElement(
        name="bof_hot_metal",
        commodity=hot_metal,
        output_commodities=[steel],
        parameters={},
    )
    bof_process = tlp.Process(
        name="BOF", technology="BOF", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[bof_bom]
    )

    bf_coke_bom = tlp.BOMElement(
        name="bf_iron_ore_coke",
        commodity=iron_ore,
        output_commodities=[hot_metal],
        parameters={"input_ratio": 1.6},
    )
    bf_hydrogen_bom = tlp.BOMElement(
        name="bf_iron_ore_hydrogen",
        commodity=iron_ore,
        output_commodities=[hot_metal],
        parameters={"input_ratio": 1.4},
    )
    bf_coke_process = tlp.Process(
        name="BF_coke", technology="BF", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[bf_coke_bom]
    )
    bf_hydrogen_process = tlp.Process(
        name="BF_hydrogen", technology="BF", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[bf_hydrogen_bom]
    )
    assert (
        bf_coke_process.bill_of_materials[0].parameters["input_ratio"]
        != (bf_hydrogen_process.bill_of_materials[0].parameters["input_ratio"])
    )

    pc_bf_coke = tlp.ProcessCenter(name="fg_coke", process=bf_coke_process, capacity=50.0, location=location)
    pc_bf_hydrogen = tlp.ProcessCenter(
        name="fg_hydrogen", process=bf_hydrogen_process, capacity=80.0, location=location
    )
    pc_bof = tlp.ProcessCenter(name="bof_pc", process=bof_process, capacity=100.0, location=location)

    trade_lp = tlp.TradeLPModel()
    trade_lp.process_centers = [pc_bf_coke, pc_bf_hydrogen, pc_bof]
    # Mirrors what the fixed connector-wiring loop in set_up_steel_trade_lp produces: every
    # (variant, variant) combination for a technology-level connector.
    trade_lp.process_connectors = [
        tlp.ProcessConnector(from_process=bf_coke_process, to_process=bof_process),
        tlp.ProcessConnector(from_process=bf_hydrogen_process, to_process=bof_process),
    ]

    trade_lp.set_legal_allocations()

    assert (pc_bf_coke, pc_bof, hot_metal) in trade_lp.legal_allocations
    assert (pc_bf_hydrogen, pc_bof, hot_metal) in trade_lp.legal_allocations
