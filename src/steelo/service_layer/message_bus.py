import logging
from typing import Callable

from ..domain import Event, Command
from ..domain.models import Environment
from .unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

Message = Event | Command


class MessageBus:
    """
    This is a simple implementation of a message bus that can handle
    messages of type Event and Command using event_handlers and command_handlers.
    It uses a UnitOfWork to collect new events.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        env: Environment,
        event_handlers: dict[type[Event], list[Callable]],
        command_handlers: dict[type[Command], Callable],
    ):
        self.uow = uow
        self.env = env
        self.event_handlers = event_handlers
        self.command_handlers = command_handlers
        self.queue: list[Message] = []
        self.processed_commands: list[Message] = list()

    def handle(self, message: Message):
        self.queue.append(message)
        while self.queue:
            message = self.queue.pop(0)

            if isinstance(message, Event):
                self.handle_event(message)
            elif isinstance(message, Command):
                self.handle_command(message)
                self.processed_commands.append(message)
            else:
                raise Exception(f"Cannot handle message of type {message}")

    def handle_event(self, event: Event):
        for handler in self.event_handlers[type(event)]:
            try:
                # print(f"handling event {event} with handler {handler}")
                logger.debug("handling event %s with handler %s", event, handler)
                handler(event)
                self.queue.extend(self.uow.collect_new_events())
            except Exception:
                logger.exception("Exception handling event %s", event)
                raise

    def handle_command(self, command: Command):
        logger.debug("handling command %s", command)
        try:
            handler = self.command_handlers[type(command)]
            handler(command)
            self.queue.extend(self.uow.collect_new_events())
        except Exception:
            logger.exception("Exception handling command %s", command)
            raise

    def collect_commands(self):
        """
        Collect the logged commands in each time step as dictionary of furnace_group_id: command_type
        """
        command_collection = {
            cmd.furnace_group_id: cmd for cmd in self.processed_commands if hasattr(cmd, "furnace_group_id")
        }
        self.processed_commands = []
        return command_collection
