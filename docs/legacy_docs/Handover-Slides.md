# Slide 1: Overview of the Architecture
- **Core Concept**: Separation of concerns through a modular structure.
- [Link to Diagram in Cosmic Python](https://www.cosmicpython.com/book/chapter_13_dependency_injection.html)
- **Key Components**:
  - Core Domain
  - Message Bus
  - Command/Event Handlers
  - Unit of Work
- **Goals**:
  - Decoupling
  - Traceability
  - Scalability

---

# Slide 2: Core Business Domain
- **Principle**: Core domain modules are independent of external libraries.
- **Implementation**:
  - Use interfaces for external dependencies (e.g., repositories, preprocessing).
  - Adapters implement interfaces (e.g., Pandas integration).
- **Examples**:
  - Repository Pattern for data access.

---

# Slide 3: Message Bus
- **Purpose**:
  - Central logging
  - Reconstruct application state from logs
  - Scalability across processors/machines.
- **Operation**:
  - Routes messages (commands and events) to handlers.
- **Advantages**:
  - Decoupling
  - Extensibility.

---

# Slide 4: Command and Event Handlers
- **Decoupling**:
  - Commands: Single handler for intent.
  - Events: Multiple handlers for facts.
- **Benefits**:
  - No need to modify existing code when adding new logic.
  - Dependency injection simplifies testing and extensibility.

---

# Slide 5: Unit of Work (UoW)
- **Role**:
  - Abstracts atomic operations.
  - Tracks exceptions and events during processing.
- **Repository Management**:
  - One aggregate per repository.
  - Ensures consistency with transaction semantics.
- **Example**:
  - Commit or rollback depending on event handling success.

---

# Slide 6: DevData and Testing
- **DevData**:
  - Standard Python patterns for development setup.
- **Testing Strategy**:
  - Example: `test_simulation_service.py` validates event and command interactions.
- **Benefits**:
  - Clear boundaries for unit and integration testing.

---

# Slide 7: Extensibility Examples
- **New Integrations**:
  - Datasette for cleaning Excel data.
  - Web applications using FastAPI or Flask.
- **Scenarios**:
  - Scaling simulations across distributed systems.
  - Adding new economic models (e.g., ABM, Stock-Flow).

---

# Slide 8: Conclusion
- **Benefits of the Architecture**:
  - Modular and decoupled design.
  - Scalable and traceable.
  - Easy to extend and maintain.
