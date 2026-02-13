"""Tests for calculate_carbon_breakdown_by_feedstock."""

from steelo.domain.calculate_costs import calculate_carbon_breakdown_by_feedstock


class _CarbonDBC:
    """Minimal DBC for carbon breakdown tests."""

    def __init__(
        self,
        metallic_charge: str,
        reductant: str,
        carbon_inputs: dict | None = None,
        carbon_outputs: dict | None = None,
    ):
        self.metallic_charge = metallic_charge
        self.reductant = reductant
        self.carbon_inputs = carbon_inputs or {}
        self.carbon_outputs = carbon_outputs or {}


def _make_bom(metallic_charge: str, demand_share: float = 1.0) -> dict:
    """Create a minimal BOM for testing."""
    return {
        "materials": {
            metallic_charge: {
                "demand": 1000.0,
                "demand_share_pct": demand_share,
                "product_volume": 1000.0,
            },
        },
    }


def test_carbon_breakdown_basic():
    """Single feedstock with carbon inputs and outputs reports correct tCO2/t-product."""
    dbc = _CarbonDBC(
        metallic_charge="io_high",
        reductant="natural_gas",
        carbon_inputs={"co2_inlet": 0.5},
        carbon_outputs={"co2_stored": 0.3, "co2_slip": 0.05, "co2_utilised": 0.15},
    )
    bom = _make_bom("io_high")

    result = calculate_carbon_breakdown_by_feedstock(
        bill_of_materials=bom,
        chosen_reductant="natural_gas",
        dynamic_business_cases=[dbc],
    )

    assert "io_high" in result
    bd = result["io_high"]
    # Inputs are negated (consumed by CCS/CCU)
    assert bd["co2_inlet"] == -0.5
    # Outputs are positive
    assert bd["co2_stored"] == 0.3
    assert bd["co2_slip"] == 0.05
    assert bd["co2_utilised"] == 0.15
    # Mass balance: sum ≈ 0
    assert abs(sum(bd.values())) < 1e-10


def test_carbon_breakdown_demand_share_scaling():
    """Carbon values are scaled by demand_share_pct."""
    dbc = _CarbonDBC(
        metallic_charge="io_high",
        reductant="natural_gas",
        carbon_inputs={"co2_inlet": 1.0},
        carbon_outputs={"co2_stored": 0.6},
    )
    bom = _make_bom("io_high", demand_share=0.6)

    result = calculate_carbon_breakdown_by_feedstock(
        bill_of_materials=bom,
        chosen_reductant="natural_gas",
        dynamic_business_cases=[dbc],
    )

    bd = result["io_high"]
    assert abs(bd["co2_inlet"] - (-0.6)) < 1e-10
    assert abs(bd["co2_stored"] - 0.36) < 1e-10


def test_carbon_breakdown_zero_pads():
    """Missing carbon keys are zero-padded when carbon_breakdown_keys is provided."""
    dbc = _CarbonDBC(
        metallic_charge="io_high",
        reductant="natural_gas",
        carbon_inputs={"co2_inlet": 0.5},
    )
    bom = _make_bom("io_high")

    result = calculate_carbon_breakdown_by_feedstock(
        bill_of_materials=bom,
        chosen_reductant="natural_gas",
        dynamic_business_cases=[dbc],
        carbon_breakdown_keys=["co2_inlet", "co2_slip", "co2_stored", "co2_utilised"],
    )

    bd = result["io_high"]
    assert bd["co2_inlet"] == -0.5
    assert bd["co2_slip"] == 0.0
    assert bd["co2_stored"] == 0.0
    assert bd["co2_utilised"] == 0.0


def test_carbon_breakdown_empty_bom():
    """Empty BOM returns empty dict."""
    dbc = _CarbonDBC(
        metallic_charge="io_high",
        reductant="natural_gas",
        carbon_inputs={"co2_inlet": 0.5},
    )

    result = calculate_carbon_breakdown_by_feedstock(
        bill_of_materials={},
        chosen_reductant="natural_gas",
        dynamic_business_cases=[dbc],
    )

    assert result == {}


def test_carbon_breakdown_filters_reductant():
    """Only the chosen reductant's feedstock appears in the breakdown."""
    dbc_gas = _CarbonDBC(
        metallic_charge="io_high",
        reductant="natural_gas",
        carbon_inputs={"co2_inlet": 0.5},
        carbon_outputs={"co2_stored": 0.3},
    )
    dbc_coke = _CarbonDBC(
        metallic_charge="io_high",
        reductant="coke",
        carbon_inputs={"co2_inlet": 0.8},
        carbon_outputs={"co2_stored": 0.1},
    )
    bom = _make_bom("io_high")

    result = calculate_carbon_breakdown_by_feedstock(
        bill_of_materials=bom,
        chosen_reductant="natural_gas",
        dynamic_business_cases=[dbc_gas, dbc_coke],
    )

    bd = result["io_high"]
    # Should reflect natural_gas DBC, not coke
    assert bd["co2_inlet"] == -0.5
    assert bd["co2_stored"] == 0.3
