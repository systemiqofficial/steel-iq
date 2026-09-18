"""China's capacity-replacement policy plugin, dormant unless bootstrapped with ``enabled=True``.

``bootstrap`` and ``handlers`` are imported as submodules, not re-exported
here: both reach into the service layer, which imports this package's
``inputs`` while initialising, so a package-level re-export would be circular.
Narrative: docs/domain_simulation_logic/capacity_replacement_policy.md.
"""

from .config import CapacityPolicyConfig
from .pool import CapacityPool, Credit, SeedEntry, WithdrawResult
from .recorder import CapacityPolicyRecorder
from .tree import TreeEvaluator, WithdrawSpec

__all__ = [
    "CapacityPolicyConfig",
    "CapacityPolicyRecorder",
    "CapacityPool",
    "Credit",
    "SeedEntry",
    "TreeEvaluator",
    "WithdrawResult",
    "WithdrawSpec",
]
