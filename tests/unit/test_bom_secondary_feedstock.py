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
                reductant="bio-pci",
                required_qty=1.0,
                secondary_feedstock={"bio-pci": 500.0},
                energy_requirements={"electricity": 0.0},
            )
        ]
    }
    env.avg_boms = {"BF_CHARCOAL": {"bio-pci": {"demand_share_pct": 1.0, "unit_cost": 100.0}}}
    bom_dict, utilization, reductant = env.get_bom_from_avg_boms(
        energy_costs={"bio_pci": 0.0, "electricity": 0.0},
        tech="BF_CHARCOAL",
        capacity=1.0,
    )

    assert "bio-pci" in bom_dict["materials"]
    assert bom_dict["materials"]["bio-pci"]["demand"] > 0
