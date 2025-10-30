from typing import Protocol, runtime_checkable

from ..service_layer.message_bus import MessageBus


@runtime_checkable
class EconomicModel(Protocol):
    def run(self, bus: MessageBus) -> None:
        """Run the economic model."""
        ...
