"""Chinese capacity-replacement policy plugin.

Dormant until the deposit wiring and withdrawal gates land — nothing in the
codebase imports this package yet.
"""

from .pool import CapacityPool, Credit, SeedEntry, WithdrawResult

__all__ = ["CapacityPool", "Credit", "SeedEntry", "WithdrawResult"]
