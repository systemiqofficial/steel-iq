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
