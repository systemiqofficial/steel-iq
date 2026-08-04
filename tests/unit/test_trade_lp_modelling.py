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
