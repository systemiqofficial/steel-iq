"""Chinese capacity-replacement policy plugin.

Fully threaded — deposit handlers, the REPLACE pre-NPV hook and both INCREASE
withdrawal gates — and activated per run by bootstrapping:
:func:`steelo.capacity_policy.bootstrap.configure_capacity_policy` binds a
fresh evaluator and pool when ``config.capacity_policy.enabled`` is True, and
guarantees the module is unbound otherwise. Unbound, every accessor returns
None and behaviour is byte-identical to a build without the package.

``bootstrap`` and ``handlers`` are imported as submodules, not re-exported
here: both reach into the service layer, which itself imports this package's
``inputs`` while initialising, so a package-level re-export would be a
circular import.
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
