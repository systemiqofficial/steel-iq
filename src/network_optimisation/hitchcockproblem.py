from typing import Any

from pyomo.environ import (  # type: ignore
    ConcreteModel,
    Set,
    Param,
    Var,
    NonNegativeReals,
    Objective,
    Constraint,
    minimize,
    SolverFactory,
)

import networkx as nx  # type: ignore
import math
from enum import Enum
import time


# Constants
GRAPH_DATA_TOLERANCE = 3
SOURCE_NODE_TYPE = "source"
SINK_NODE_TYPE = "sink"
COST_DECIMAL_CORRECTION = 100


class PseudoNodes(Enum):
    PSEUDO_SOURCE_NAME = "Pseudo-Source"
    PSEUDO_SINK_NAME = "Pseudo-Sink"


class HitchcockProblem:
    def __init__(self, sources, sinks, allocation_costs, source_costs=None):
        """
        Initializes the Hitchcock Transportation Problem.

        Args:
        sources (dict): A dictionary with source nodes as keys and their supply amounts as values.
        sinks (dict): A dictionary with sink nodes as keys and their demand amounts as values.
        allocation_costs (dict of dict): A nested dictionary where allocation_costs[i][j] is the cost to transport from source i to sink j.

        Note: Unlike traditional Hitchcock Problems, this structure can also solve the problem if total demand != total supply,
        however only if solves as a min-cost max-flow problem, otherwise it leads to an LP infeasibility.
        """
        self.sources = sources
        self.sinks = sinks
        self.allocation_costs = allocation_costs
        self.source_costs = source_costs

    def display_problem(self):
        """Prints the supply, demand, and costs in a readable format."""
        print("Supply:")
        for s in self.sources:
            print(f" Source {s}: {self.sources[s]}")

        print("\nDemand:")
        for t in self.sinks:
            print(f" Sink {t}: {self.sinks[t]}")

        print("\nAllocation Costs:")
        for s in self.sources:
            for t in self.sinks:
                if t in self.allocation_costs[s]:
                    print(f" Cost from Source {s} to Sink {t}: {self.allocation_costs[s][t]}")

    def to_networkx_graph(self):
        """
        Creates a networkx graph from the Hitchcock problem instance.

        Returns:
        G (networkx.DiGraph): A directed graph where nodes represent sources and sinks,
                               and edges represent transportation routes with attributes for costs and capacities.
        """
        G = nx.DiGraph()

        # Add source and sink nodes with their supplies and demands as node attributes
        for source, supply in self.sources.items():
            G.add_node(source, supply=int(supply), demand=0, node_type=SOURCE_NODE_TYPE)
        for sink, demand in self.sinks.items():
            G.add_node(sink, demand=int(demand), supply=0, node_type=SINK_NODE_TYPE)

        # Add edges with costs and capacities as edge attributes
        for source in self.allocation_costs:
            for sink in self.allocation_costs[source]:
                assert sink in self.sinks, f"Sink {sink} not in the list of sinks."
                assert source in self.sources, f"Source {source} not in the list of sources."
                G.add_edge(
                    source,
                    sink,
                    cost=int(COST_DECIMAL_CORRECTION * self.allocation_costs[source][sink]),
                    capacity=1_000_000,
                )

        return G

    def turn_to_max_flow_min_cost_problem(self):
        """
        Augments a given graph from a Hitchcock Problem with a pseudo-source and pseudo-sink.

        Returns:
        networkx.DiGraph: The augmented graph with a pseudo-source and pseudo-sink as well as adapted edge costs and capacities.
        """
        G = self.to_networkx_graph()
        H = G.copy()

        # Add pseudo-source and pseudo-sink nodes
        pseudo_source = PseudoNodes.PSEUDO_SOURCE_NAME.value
        pseudo_sink = PseudoNodes.PSEUDO_SINK_NAME.value

        total_source_supply = round(
            sum(data["supply"] for node, data in G.nodes(data=True) if data["node_type"] == SOURCE_NODE_TYPE)
        )
        total_sink_demand = round(
            sum(
                data["demand"] if not math.isnan(data["demand"]) else 0
                for node, data in G.nodes(data=True)
                if data["node_type"] == SINK_NODE_TYPE
            )
        )

        H.add_node(pseudo_source, supply=int(total_source_supply), demand=0, node_type=pseudo_source)
        H.add_node(pseudo_sink, supply=0, demand=int(total_sink_demand), node_type=pseudo_sink)

        # Connect the pseudo-source to all sources
        for node, data in G.nodes(data=True):
            if data["node_type"] == SOURCE_NODE_TYPE:
                if self.source_costs is None or math.isinf(self.source_costs[node]):
                    # FIXME 0 cost for infinite costs - is this correct?
                    cost = 0
                else:
                    cost = (
                        int(COST_DECIMAL_CORRECTION * self.source_costs[node]) if self.source_costs is not None else 0
                    )
                H.add_edge(pseudo_source, node, capacity=int(data["supply"]), cost=cost)
                H.nodes[node]["supply"] = 0

        # Connect all sinks to the pseudo-sink
        for node, data in G.nodes(data=True):
            if data["node_type"] == SINK_NODE_TYPE:
                H.add_edge(node, pseudo_sink, capacity=int(data["demand"]), cost=0)
                H.nodes[node]["demand"] = 0

        return H

    @staticmethod
    def check_correctness(graph):
        """Checks the correctness of the graph structure."""
        for edge in graph.edges:
            if "capacity" in graph.edges[edge]:
                edge_cap = graph.edges[edge]["capacity"]
                if not isinstance(edge_cap, int):
                    print(f"Capacity of edge {edge} is not integral: {edge_cap}")
                    return False
                if edge_cap < 0:
                    print(f"Capacity of edge {edge} is negative: {edge_cap}")
                    return False

            if "cost" in graph.edges[edge]:
                edge_cost = graph.edges[edge]["cost"]
                if not isinstance(edge_cost, int):
                    print(f"Cost of edge {edge} is not integral: {edge_cost}")
                    return False
                if edge_cost < 0:
                    print(f"Cost of edge {edge} is negative: {edge_cost}")
                    return False

        for node in graph.nodes:
            node_demand = graph.nodes[node]["demand"]
            node_supply = graph.nodes[node]["supply"]
            if not isinstance(node_demand, int) or not isinstance(node_supply, int):
                print(f"Supply or demand of node {node} is not integral: {node_supply}, {node_demand}")
                return False
            if node_demand < 0 or node_supply < 0:
                print(f"Supply or demand of node {node} is negative: {node_supply}, {node_demand}")
                return False

        return True

    def solve_as_min_cost_flow(self):
        """Solves the problem as a minimum-cost flow problem."""
        H = self.turn_to_max_flow_min_cost_problem()
        if not self.check_correctness(H):
            print("Error: There's something wrong with the graph.")
            return None, None

        # Check for cycles
        if list(nx.simple_cycles(H)):
            print("Error: Graph contains cycles.")
            return None, None

        start_time = time.time()
        min_cost_flow = nx.max_flow_min_cost(
            H,
            PseudoNodes.PSEUDO_SOURCE_NAME.value,
            PseudoNodes.PSEUDO_SINK_NAME.value,
            capacity="capacity",
            weight="cost",
        )
        execution_time = time.time() - start_time
        min_cost = nx.cost_of_flow(H, min_cost_flow, weight="cost")

        print(f"Execution time: {execution_time:.2f} seconds")
        return min_cost, self.remove_pseudo_nodes(min_cost_flow)

    def remove_pseudo_nodes(self, min_cost_flow):
        # Remove arcs from and to the pseudo source and sink
        pseudo_source = PseudoNodes.PSEUDO_SOURCE_NAME.value
        pseudo_sink = PseudoNodes.PSEUDO_SINK_NAME.value

        for node in list(min_cost_flow[pseudo_source].keys()):
            del min_cost_flow[pseudo_source][node]
        for node in list(min_cost_flow.keys()):
            if pseudo_sink in min_cost_flow[node]:
                del min_cost_flow[node][pseudo_sink]
        return min_cost_flow

    def solve_as_lp(self, *, solver=None) -> tuple[float, dict[Any, dict[Any, float]]]:
        """
        Solves the Hitchcock (transportation) problem using Pyomo.

        Returns:
            tuple: (total minimal cost, flow configuration)
        """
        # Create a Pyomo model
        model = ConcreteModel()

        # Define sets
        model.SOURCES = Set(initialize=sorted(self.sources.keys()))
        model.SINKS = Set(initialize=sorted(self.sinks.keys()))

        # Define parameters
        #    supply[s] = capacity of source s
        #    demand[t] = demand of sink t
        #    cost[s, t] = cost to ship from s to t (only if s->t exists in allocation_costs)
        def supply_init(model, s):
            return self.sources[s]

        model.supply = Param(model.SOURCES, initialize=supply_init)

        def demand_init(model, t):
            return self.sinks[t]

        model.demand = Param(model.SINKS, initialize=demand_init)

        # We need a cost for each (s, t). If some (s, t) isn't in self.allocation_costs[s],
        # you can set it to a large number or exclude it entirely. Here we assume the user
        # only includes valid s->t pairs in self.allocation_costs.
        def cost_init(model, s, t):
            return self.allocation_costs[s][t]

        model.cost = Param(model.SOURCES, model.SINKS, initialize=cost_init)

        # 4. Define variables:
        #    x[s, t] >= 0 is the flow from source s to sink t.
        model.x = Var(model.SOURCES, model.SINKS, domain=NonNegativeReals)

        # Objective: Minimize total cost = sum_{s in SOURCES, t in SINKS} cost[s,t] * x[s,t]
        def obj_rule(model):
            return sum(model.cost[s, t] * model.x[s, t] for s in model.SOURCES for t in model.SINKS)

        model.obj = Objective(rule=obj_rule, sense=minimize)

        # Constraints
        # Supply constraints: sum of flows from each source s <= supply[s]
        def supply_constraint_rule(model, s):
            return sum(model.x[s, t] for t in model.SINKS) <= model.supply[s]

        model.supply_constraint = Constraint(model.SOURCES, rule=supply_constraint_rule)

        # Demand constraints: sum of flows into each sink t = demand[t]
        def demand_constraint_rule(model, t):
            return sum(model.x[s, t] for s in model.SOURCES) == model.demand[t]

        model.demand_constraint = Constraint(model.SINKS, rule=demand_constraint_rule)

        if not solver:
            # Solve the model with a solver of your choice, e.g. "glpk" or "cbc"
            solver = SolverFactory("cbc")  # or "glpk", etc.

            # Check if solver is available
            if not solver.available():
                raise Exception("Solver not available. Please install 'cbc' or another compatible solver.")
        results = solver.solve(model, tee=False)

        # Check solver status
        if (results.solver.status != "ok") or (results.solver.termination_condition != "optimal"):
            raise Exception("No feasible (optimal) solution found.")

        # Extract solution
        total_cost = model.obj()
        flow: dict[Any, dict[Any, float]] = {}
        for s in model.SOURCES:
            flow[s] = {}
            for t in model.SINKS:
                flow[s][t] = model.x[s, t].value

        return total_cost, flow
