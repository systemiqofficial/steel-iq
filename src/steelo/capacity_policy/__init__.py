"""Chinese capacity-replacement policy plugin.

Dormant until the deposit wiring and withdrawal gates land — the data layer
(sheet readers, fixtures, config) is wired but nothing acts on it yet.
"""

from .config import CapacityPolicyConfig
from .pool import CapacityPool, Credit, SeedEntry, WithdrawResult

__all__ = ["CapacityPolicyConfig", "CapacityPool", "Credit", "SeedEntry", "WithdrawResult"]
