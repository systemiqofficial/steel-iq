"""Chinese capacity-replacement policy plugin.

Fully threaded — deposit handlers, the REPLACE pre-NPV hook and both INCREASE
withdrawal gates — but dormant until bootstrapping binds an evaluator and pool
(``bind_capacity_policy``, D8): every accessor returns None unbound and
behaviour is byte-identical to a build without the package.
"""

from .config import CapacityPolicyConfig
from .pool import CapacityPool, Credit, SeedEntry, WithdrawResult
from .tree import TreeEvaluator, WithdrawSpec

__all__ = [
    "CapacityPolicyConfig",
    "CapacityPool",
    "Credit",
    "SeedEntry",
    "TreeEvaluator",
    "WithdrawResult",
    "WithdrawSpec",
]
