import pytest

from steelo.domain.models import Environment


class DummyFeed:
    def __init__(
        self,
        metallic_charge: str,
        reductant: str,
        required_qty: float,
        secondary_feedstock: dict[str, float] | None = None,
        energy_requirements: dict[str, float] | None = None,
    ):
        self.metallic_charge = metallic_charge
        self.reductant = reductant
        self.required_quantity_per_ton_of_product = required_qty
        self.secondary_feedstock = secondary_feedstock or {}
        self.energy_requirements = energy_requirements or {}


def test_bom_handles_secondary_feedstock_inputs():
    env = Environment.__new__(Environment)
    env.dynamic_feedstocks = {
        "BF_CHARCOAL": [
            DummyFeed(
                metallic_charge="io_high",
                reductant="bio_pci",
                required_qty=1.0,
                secondary_feedstock={"bio_pci": 500.0},
                energy_requirements={"electricity": 0.0},
            )
        ]
    }
    env.avg_boms = {"BF_CHARCOAL": {"bio_pci": {"demand_share_pct": 1.0, "unit_cost": 100.0}}}
    bom_dict, utilization, reductant = env.get_bom_from_avg_boms(
        energy_costs={"bio_pci": 0.0, "electricity": 0.0},
        tech="BF_CHARCOAL",
        capacity=1.0,
    )

    assert "bio_pci" in bom_dict["materials"]
    assert bom_dict["materials"]["bio_pci"]["demand"] > 0


def test_bom_from_feedstocks_for_dri_esf_ccs_not_empty():
    """Ensure a BOM is built from feedstocks for DRI+ESF+CCS when avg_boms exists."""

    env = Environment.__new__(Environment)

    class DummyFeed:
        def __init__(self):
            self.metallic_charge = "io_low"
            self.reductant = "coal"
            self.required_quantity_per_ton_of_product = 1.5
            self.secondary_feedstock = {"coking_coal": 50.0}
            self.energy_requirements = {"electricity": 100.0}

    feed = DummyFeed()
    env.dynamic_feedstocks = {"DRI+ESF+CCS": [feed], "dri+esf+ccs": [feed]}
    env.avg_boms = {"DRI+ESF+CCS": {"io_low": {"demand_share_pct": 1.0, "unit_cost": 200.0}}}
    env.avg_utilization = {"DRI+ESF+CCS": {"utilization_rate": 0.6}}

    bom_dict, utilization, reductant = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 50.0, "coking_coal": 10.0},
        tech="DRI+ESF+CCS",
        capacity=1000.0,
    )

    assert bom_dict["materials"]
    assert "io_low" in bom_dict["materials"]
    assert bom_dict["materials"]["io_low"]["demand"] > 0
    assert utilization == 0.6


def test_bom_from_avg_boms_missing_tech_raises():
    """If avg_boms lacks the tech, get_bom_from_avg_boms should error fast."""

    env = Environment.__new__(Environment)

    class DummyFeed:
        def __init__(self):
            self.metallic_charge = "io_low"
            self.reductant = "coal"
            self.required_quantity_per_ton_of_product = 1.0
            self.secondary_feedstock = {}
            self.energy_requirements = {"electricity": 1.0}

    feed = DummyFeed()
    env.dynamic_feedstocks = {"DRI+ESF+CCS": [feed]}
    env.avg_boms = {}  # tech missing
    env.avg_utilization = {}

    with pytest.raises(KeyError):
        env.get_bom_from_avg_boms(
            energy_costs={"electricity": 50.0},
            tech="DRI+ESF+CCS",
            capacity=1000.0,
        )
