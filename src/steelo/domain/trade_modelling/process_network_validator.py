"""
Process Network Validation Module

This module provides functionality to validate the connectivity of steel production
technologies based on their dynamic bills of materials and legal process connectors.
"""

from typing import Any, TYPE_CHECKING
import logging
from steelo.adapters.repositories import InMemoryRepository
from steelo.domain.models import LegalProcessConnector, UnknownTechnologyError, is_technology_allowed

if TYPE_CHECKING:
    from steelo.simulation import SimulationConfig


def validate_process_network_connectivity(
    repository: InMemoryRepository,
    legal_process_connectors: list[LegalProcessConnector],
    config: "SimulationConfig",
    current_year: int | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Validates the process network by checking if technologies are properly connected
    through matching output/input commodities based on their dynamic BOMs.

    Args:
        repository: InMemoryRepository containing plants and suppliers
        legal_process_connectors: List of legal connectors between technologies
        config: SimulationConfig with primary products definition
        current_year: Scenario year used when checking technology availability
        verbose: Whether to log detailed validation results

    Returns:
        Dictionary containing:
        - 'isolated_technologies': List of technologies with no valid connections
        - 'missing_inputs': Dict of technologies missing required input connections
        - 'missing_outputs': Dict of technologies with outputs that go nowhere
        - 'invalid_connectors': List of legal connectors that don't match commodities
        - 'technology_info': Complete mapping of all technologies and their I/O
        - 'graph': NetworkX graph for further analysis
    """
    logger = logging.getLogger(f"{__name__}.validate_process_network_connectivity")
    import networkx as nx

    # Build a graph of technology connectivity
    G = nx.DiGraph()

    # Collect all unique technologies and their input/output commodities
    technology_info: dict[str, dict[str, Any]] = {}

    # Analyze all furnace groups to understand technology requirements
    for plant in repository.plants.list():
        for fg in plant.furnace_groups:
            tech_name = fg.technology.name

            if tech_name not in technology_info:
                technology_info[tech_name] = {
                    "inputs": set(),
                    "outputs": set(),
                    "product": fg.technology.product,
                    "furnace_groups": [],
                }

            technology_info[tech_name]["furnace_groups"].append(f"{plant.plant_id}_{fg.furnace_group_id}")

            # Collect inputs from effective primary feedstocks (dynamic BOMs)
            # Only consider metallic charges as relevant commodities for connectivity
            if fg.effective_primary_feedstocks:
                for feedstock in fg.effective_primary_feedstocks:
                    # Primary input (metallic charge) - this is the key connectivity commodity
                    if feedstock.metallic_charge:
                        technology_info[tech_name]["inputs"].add(feedstock.metallic_charge)

            # Collect outputs from the dynamic feedstocks (what actually gets produced)
            # (metallic charges flow between technologies, not secondary commodities)
            if fg.effective_primary_feedstocks:
                for feedstock in fg.effective_primary_feedstocks:
                    primary_outputs = feedstock.get_primary_outputs(config.primary_products)
                    for output in primary_outputs:
                        technology_info[tech_name]["outputs"].add(output)

    # Add special nodes for supply and demand
    technology_info["demand"] = {
        "inputs": {"steel", "iron"},  # Demand can consume steel and iron
        "outputs": set(),
        "product": "demand",
        "furnace_groups": [],
    }

    # Add supplier technologies
    for supplier in repository.suppliers.list():
        # Handle both string and enum types for commodity
        if isinstance(supplier.commodity, str):
            commodity_name = supplier.commodity
        else:
            commodity_name = supplier.commodity.value

        supplier_tech = f"{commodity_name}_supply"
        if supplier_tech not in technology_info:
            technology_info[supplier_tech] = {
                "inputs": set(),
                "outputs": {commodity_name},
                "product": commodity_name,
                "furnace_groups": [],
            }

    # Add all technologies as nodes
    for tech_name in technology_info:
        G.add_node(tech_name, **technology_info[tech_name])

    # Determine the year to use when checking technology availability
    if current_year is not None:
        year_for_checks = current_year
    else:
        start_year = getattr(config, "start_year", None)
        year_for_checks = int(start_year) if start_year is not None else 0

    # Helper to determine whether a technology is disabled according to the scenario configuration.
    def _technology_disabled_in_config(
        tech_name: str,
        config: "SimulationConfig",
        current_year: int | None,
    ) -> bool:
        # Supply and demand pseudo technologies always exist
        if tech_name == "demand" or tech_name.endswith("_supply"):
            return False

        technology_settings = getattr(config, "technology_settings", None)
        if not technology_settings:
            return False

        year_to_check = current_year if current_year is not None else year_for_checks

        try:
            return not is_technology_allowed(technology_settings, tech_name, year_to_check)
        except UnknownTechnologyError:
            # If the technology is unknown to the configuration, treat it as disabled.
            return True

    # Track validation issues
    invalid_connectors: list[dict[str, Any]] = []
    valid_connections: list[dict[str, Any]] = []

    # Check each legal process connector
    for connector in legal_process_connectors:
        from_tech = connector.from_technology_name
        to_tech = connector.to_technology_name

        # Skip if technologies don't exist
        if from_tech not in technology_info or to_tech not in technology_info:
            missing_tech = from_tech if from_tech not in technology_info else to_tech

            if _technology_disabled_in_config(
                tech_name=missing_tech,
                config=config,
                current_year=current_year,
            ):
                logger.debug(
                    "Skipping connector %s -> %s because technology '%s' is disabled in configuration",
                    from_tech,
                    to_tech,
                    missing_tech,
                )
                continue

            invalid_connectors.append(
                {
                    "from": from_tech,
                    "to": to_tech,
                    "reason": f"Technology not found: {from_tech if from_tech not in technology_info else to_tech}",
                }
            )
            continue

        # Get outputs from source technology and inputs to destination technology
        from_outputs = technology_info[from_tech]["outputs"]
        to_inputs = technology_info[to_tech]["inputs"]

        # Check if there's a commodity match
        matching_commodities = from_outputs.intersection(to_inputs)

        if matching_commodities:
            # Valid connection - add edge to graph
            for commodity in matching_commodities:
                G.add_edge(from_tech, to_tech, commodity=commodity)
            valid_connections.append({"from": from_tech, "to": to_tech, "commodities": list(matching_commodities)})
        else:
            # Invalid connector - technologies don't share commodities
            invalid_connectors.append(
                {
                    "from": from_tech,
                    "to": to_tech,
                    "from_outputs": list(from_outputs),
                    "to_inputs": list(to_inputs),
                    "reason": "No matching commodities between output and input",
                }
            )

    # Find isolated technologies (no incoming or outgoing edges)
    isolated_technologies = []
    for tech_name in G.nodes():
        if G.in_degree(tech_name) == 0 and G.out_degree(tech_name) == 0:
            # Skip demand and pure supply nodes
            if tech_name != "demand" and not tech_name.endswith("_supply"):
                tech_data = technology_info[tech_name]
                isolated_technologies.append(
                    {
                        "name": tech_name,
                        "product": tech_data["product"],
                        "inputs_required": list(tech_data["inputs"]),
                        "outputs_produced": list(tech_data["outputs"]),
                        "furnace_groups": tech_data["furnace_groups"][:5],  # Show first 5 FGs
                    }
                )

    # Find technologies missing required inputs
    missing_inputs: dict[str, dict[str, Any]] = {}
    for tech_name, tech_data in technology_info.items():
        if tech_data["inputs"] and tech_name != "demand":
            required_inputs = tech_data["inputs"]

            # Check what inputs can actually be supplied
            available_inputs = set()
            for predecessor in G.predecessors(tech_name):
                edge_data = G.get_edge_data(predecessor, tech_name)
                if edge_data and "commodity" in edge_data:
                    available_inputs.add(edge_data["commodity"])

            missing = required_inputs - available_inputs
            if missing:
                missing_inputs[tech_name] = {
                    "required": list(required_inputs),
                    "available": list(available_inputs),
                    "missing": list(missing),
                    "affected_furnace_groups": len(tech_data["furnace_groups"]),
                }

    # Find technologies with outputs that go nowhere
    missing_outputs: dict[str, dict[str, Any]] = {}
    for tech_name, tech_data in technology_info.items():
        if tech_data["outputs"] and not tech_name.endswith("_supply"):
            tech_outputs = tech_data["outputs"]

            # Check where outputs can go
            used_outputs = set()
            for successor in G.successors(tech_name):
                edge_data = G.get_edge_data(tech_name, successor)
                if edge_data and "commodity" in edge_data:
                    used_outputs.add(edge_data["commodity"])

            unused = tech_outputs - used_outputs
            if unused:
                missing_outputs[tech_name] = {
                    "produces": list(tech_outputs),
                    "used": list(used_outputs),
                    "unused": list(unused),
                }

    # Log detailed results if verbose
    if verbose:
        # logger.debug("=" * 80)
        # logger.debug("TECHNOLOGY NETWORK CONNECTIVITY VALIDATION")
        # logger.debug("=" * 80)

        logger.info(
            f"Network Summary: {len(technology_info)} technologies, "
            f"{len(valid_connections)} valid connections, "
            f"{sum(1 for t in technology_info.values() if t['furnace_groups'])} with furnace groups"
        )

        if isolated_technologies:
            logger.warning(f"Found {len(isolated_technologies)} isolated technologies:")
            for tech in isolated_technologies:
                logger.warning(f"  - {tech['name']} (produces: {tech['product']})")
                # logger.debug(
                #     f"    Requires: {', '.join(tech['inputs_required']) if tech['inputs_required'] else 'nothing'}"
                # )
                # logger.debug(
                #     f"    Produces: {', '.join(tech['outputs_produced']) if tech['outputs_produced'] else 'nothing'}"
                # )
                # logger.debug(f"    Affects {len(tech['furnace_groups'])} furnace groups")

        if missing_inputs:
            logger.warning(f"Found {len(missing_inputs)} technologies missing required inputs:")
            for tech_name, details in missing_inputs.items():
                logger.warning(f"  - {tech_name}: missing {', '.join(details['missing'])}")
                # logger.debug(f"    Affects {details['affected_furnace_groups']} furnace groups")

        if missing_outputs:
            logger.info(f"Found {len(missing_outputs)} technologies with unused outputs:")
            for tech_name, details in missing_outputs.items():
                logger.info(f"  - {tech_name}: unused outputs {', '.join(details['unused'])}")

        if invalid_connectors:
            logger.warning(f"Found {len(invalid_connectors)} invalid legal connectors:")
            for i, conn in enumerate(invalid_connectors):
                if i < 10:  # Show first 10
                    logger.warning(f"  - {conn['from']} -> {conn['to']}: {conn['reason']}")
                    # if conn.get("from_outputs"):
                    # logger.debug(f"    {conn['from']} outputs: {', '.join(list(conn['from_outputs'])[:3])}")
                    # if conn.get("to_inputs"):
                    # logger.debug(f"    {conn['to']} requires: {', '.join(list(conn['to_inputs'])[:3])}")
                elif i == 10:
                    logger.warning(f"    ... and {len(invalid_connectors) - 10} more invalid connectors")
                    break

        # logger.debug("=" * 80)

    return {
        "isolated_technologies": isolated_technologies,
        "missing_inputs": missing_inputs,
        "missing_outputs": missing_outputs,
        "invalid_connectors": invalid_connectors,
        "valid_connections": valid_connections,
        "technology_info": technology_info,
        "graph": G,  # NetworkX graph for further analysis
    }
