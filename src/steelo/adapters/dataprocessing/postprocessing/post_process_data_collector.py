# Library for post processing functions - summarising tables, - plots, related to the datacollector class
import pandas as pd

# import matplotlib.pyplot as plt
from ....domain.datacollector import DataCollector


def summarise_agent_decisions_to_table(
    data_collector: DataCollector,
):  # TODO: Talk to @Jochen about if this can be done. Otherwise it should be a json path? or pickle
    """
    summarise the agent decisions strored in the datacollector into a table
    """

    return pd.DataFrame(data_collector.trace_decisions)


def summarise_utilisation_rates_by_technology(data_collector):
    """
    Summarise utilisation rates by technology and plot the time series
    """
    pass


def summarise_installed_capacity_by_tech_by_region_by(data_collector, admin_level):
    """
    Summarise and visualise the development of production capacity by tech over time, and by region.
    admin level determines the granularity of the regions, 0 being country, 1 being, sub continental regions, 2 being...
    """
    pass


def summarise_steel_production_by_region(data_collector, admin_level):
    """
    Summarise and visualise the development of production of steel by region.
    admin level determines the granularity of the regions, 0 being country, 1 being, sub continental regions, 2 being...
    """
    pass


def summarise_steel_cost_over_time(data_collector):
    """
    Summarise the development of steel costs over time, using a weighted average over all furnaces
    """
    pass


def summarise_iron_costs_over_time(data_colelctor):
    """
    Summarise the development of iron products costs over time, using a weighted average over all furnaces
    """
    pass
