# Architecture Decision Record: Event-Driven Architecture

```
Status: Proposed
Date: 2024-12-20
```

## Context

The steel decarbonization model needs to handle complex interactions between different components:

- Plants need to make strategic decisions about their furnace groups
- These decisions affect the global cost curve
- Changes to the cost curve influence other plants' decisions
- Economic models need to process these changes consistently

We need an architecture that can:
- Handle complex workflows while keeping code maintainable
- Ensure consistency of operations
- Keep components decoupled
- Make the system's behavior explicit and traceable
- Support different economic models (Agent-Based and Stock-Flow)

## Decision

Implement an event-driven architecture using the following components:

1. **Message Bus**: Central component that handles two types of messages:
   - Commands: Represent intentions to change the system (e.g., `CloseFurnaceGroup`)
   - Events: Represent facts that have occurred (e.g., `FurnaceGroupClosed`)

2. **Unit of Work**: Provides an abstraction over atomic operations:
   - Manages the lifecycle of database/repository transactions
   - Collects domain events during operations
   - Ensures consistency of the system's state

3. **Handlers**:
   - Command Handlers: Execute business logic in response to commands
   - Event Handlers: React to events and update projections/views

4. **Bootstrap Process**: 
   - Configures the system components
   - Handles dependency injection
   - Creates a properly configured MessageBus instance

## Command vs Event Guidelines

The system distinguishes between Commands and Events in the following ways:

1. **Commands**:
   - Represent an intention to change the system state
   - Always have exactly one handler
   - Must either succeed completely or fail completely - we expect a response
   - Can generate events as a result of their execution
   - Example: `ChangeFurnaceGroupTechnology` is a command because it:
     - Changes system state (the technology of a furnace group)
     - Must either succeed or fail atomically
     - Generates a `FurnaceGroupTechChanged` event if successful

2. **Events**:
   - Represent facts that have already happened
   - Raising an event does not imply expecting a response
   - Can have zero, one, or multiple handlers
   - Are collected by aggregates (like Plant) during command execution
   - Are processed after their triggering command completes
   - Example: `FurnaceGroupTechChanged` is an event because it:
     - Records a fact about what happened
     - Updates projections (like the cost curve)
     - Could trigger other processes in the future

## Implementation Details

1. **Message Types**:
   ```python
   # Commands represent intentions
   @dataclass
   class ChangeFurnaceGroupTechnology(Command):
       furnace_group_id: str
       technology_name: str

   # Events represent facts
   @dataclass
   class FurnaceGroupTechChanged(Event):
       furnace_group_id: str
   ```

2. **Message Bus**:
   - Routes messages to appropriate handlers
   - Maintains a queue of messages
   - Processes events and commands atomically

3. **Unit of Work**:
   - Provides a context manager interface
   - Collects events during operations
   - Handles rollback in case of errors

4. **Handler Registration**:
   ```python
   EVENT_HANDLERS = {
       FurnaceGroupClosed: [update_cost_curve],
       FurnaceGroupTechChanged: [update_cost_curve],
   }

   COMMAND_HANDLERS = {
       CloseFurnaceGroup: close_furnace_group,
       ChangeFurnaceGroupTechnology: change_furnace_group_technology,
   }
   ```

## Consequences

### Advantages

1. **Decoupling**:
   - Commands separate intent from implementation
   - Events decouple cause from effect
   - Components can evolve independently

2. **Traceability**:
   - All system changes are explicit commands
   - Events provide an audit trail
   - Easier debugging and monitoring

3. **Extensibility**:
   - New behaviors can be added by subscribing to events
   - Different economic models can coexist
   - Easy to add new command/event handlers

4. **Consistency**:
   - Unit of Work ensures atomic operations
   - Clear boundaries for transactions
   - Predictable system behavior

### Disadvantages

1. **Complexity**:
   - More moving parts than simple procedural code
   - Requires understanding of event-driven patterns
   - Can be harder to follow the flow of control

2. **Message Design**:
   - Need to carefully design command/event contracts
   - Risk of message explosion if not managed
   - Need to handle failed messages appropriately

3. **Learning Curve**:
   - Team needs to understand event-driven concepts
   - Different debugging/testing approaches required
   - More setup code required

## References

- "Architecture Patterns with Python" (Cosmic Python) - Especially chapters on Event-Driven Architecture
- Domain-Driven Design patterns for handling domain events
- Martin Fowler's writings on Event-Driven Architecture / [This conference talk](https://youtu.be/STKCRSUsyP0?si=hwfTP5hcA899x02W)

## Future Considerations

1. **Event Storage**:
   - Consider persisting events for audit/replay
   - Evaluate event sourcing for certain components

2. **Message Reliability**:
   - Implement retry mechanisms for failed handlers
   - Consider message persistence for reliability

3. **Monitoring**:
   - Add telemetry for message processing
   - Monitor message queue sizes and processing times

4. **Scaling**:
   - Evaluate need for distributed message processing
   - Consider message broker integration if needed