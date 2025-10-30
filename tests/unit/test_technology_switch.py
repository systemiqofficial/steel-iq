import pytest

from math import nan

# from steelo.adapters.dataprocessing.preprocessing.technology_switch import CapexSwitchDesignMatrix


@pytest.fixture
def design_matrix():
    # example design matrix data
    data = {
        ("Charge preparation", "Coke production"): [1.0, 1.0, 1.0, nan, nan, nan],
        ("Charge preparation", "Sinter production"): [1.0, 1.0, 1.0, nan, nan, nan],
        ("Charge preparation", "Pellets production"): [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        ("Iron making", "BF - HM production"): [1.0, 1.0, 1.0, nan, nan, nan],
        ("Iron making", "Smelting Furnace"): [nan, nan, nan, 1.0, nan, nan],
        ("Iron making", "Shaft Furnace"): [nan, nan, nan, nan, 1.0, 1.0],
        ("Iron making", "CCU"): [nan, nan, 1.0, nan, nan, nan],
        ("Steel making", "BOF + Casting"): [1.0, 1.0, 1.0, 1.0, nan, nan],
        ("Steel making", "EAF + Casting"): [nan, nan, nan, nan, 1.0, nan],
        ("Steel making", "Remelting"): [nan, nan, nan, 1.0, nan, nan],
        ("Other", "Oxygen generation"): [1.0, 1.0, 1.0, nan, nan, nan],
        ("Other", "Electricity generation"): [1.0, 1.0, 1.0, nan, nan, nan],
        ("Other", "Steam generation"): [1.0, 1.0, 1.0, nan, nan, nan],
        ("Other", "Limestone use"): [1.0, 1.0, 1.0, 1.0, nan, nan],
    }

    columns = [
        "Avg BF-BOF",
        "BAT BF-BOF",
        "BAT BF-BOF+CCU",
        "DRI-Melt-BOF",
        "DRI-EAF",
        "DRI",
    ]
    rows = []
    for (process_part, process_step), values in data.items():
        row = {"Process part": process_part, "Process step": process_step}
        row.update({k: v for k, v in zip(columns, values)})
        rows.append(row)
    return rows


# @pytest.fixture
# def capex_switch(design_matrix):
#     greenfield_capex = {r["Process step"]: 1 for r in design_matrix}
#     brownfield_capex = {k: v / 2 for k, v in greenfield_capex.items()}
#     return CapexSwitchDesignMatrix(
#         greenfield_capex=greenfield_capex, brownfield_capex=brownfield_capex, design_matrix=design_matrix
#     )


@pytest.mark.skip(reason="fixture capex_switch not available yet")
def test_production_tech_switch_capex(capex_switch):
    # Given a capex switch matrix and a production technology
    production_tech = capex_switch.design_matrix["Avg BF-BOF"]

    # When the production technology is switched
    result = capex_switch.production_tech_switch_capex(production_tech).to_dict()

    expected_capex = {
        "Avg BF-BOF": 4.5,
        "BAT BF-BOF": 4.5,
        "BAT BF-BOF+CCU": 5.5,
        "DRI-Melt-BOF": 3.5,
        "DRI-EAF": 2.5,
        "DRI": 1.5,
    }

    # Then the result matches the expected_capex values
    assert result == expected_capex


@pytest.mark.skip(reason="fixture capex_switch not available yet")
def test_capex_switch_all_tech(capex_switch):
    # When we calculate the capex switch for all technologies
    switch_all = capex_switch.capex_switch_all_tech()

    # Then the result matches the expected capex values
    expected_capex_for_tech = {
        "Avg BF-BOF": [4.5, 4.5, 5.5, 3.5, 2.5, 1.5],
        "BAT BF-BOF": [4.5, 4.5, 5.5, 3.5, 2.5, 1.5],
        "BAT BF-BOF+CCU": [4.5, 4.5, 5.0, 3.5, 2.5, 1.5],
        "DRI-Melt-BOF": [7.5, 7.5, 8.5, 2.5, 2.5, 1.5],
        "DRI-EAF": [8.5, 8.5, 9.5, 4.5, 1.5, 1],
        "DRI": [8.5, 8.5, 9.5, 4.5, 2, 1],
    }
    tech_names = list(expected_capex_for_tech.keys())
    for tech, capex_values in expected_capex_for_tech.items():
        expected_capex_for_tech[tech] = dict(zip(tech_names, capex_values))

    assert switch_all == expected_capex_for_tech
