# Architectural Decision Record: Repository Pattern

```
Status: Proposed
Date: 2021-11-11
```

## Context and Problem Statement

The question came up whether to use fine granular datasets or public
aggregated datasets. The problem with fine granular datasets is that
they are often proprietary and not available to the public. The problem
with public aggregated datasets is that they are often not fine enough
to answer the specific questions that we have - to use them for agent
based modeling for example.

The system must enable the use of publicly available, non-proprietary datasets that
provide sufficient granularity to support specific analytical needs, such as
agent-based modeling.

**Justification**:

- *Fine-Grained Datasets*: While these datasets offer the necessary detail for our analyses,
   they are often proprietary and inaccessible to the public, posing legal and logistical challenges.
- *Public Aggregated Datasets*: These are readily available but typically lack the level
  of detail required to address our specific questions effectively.

**Implications for Architecture**:

- The architecture should incorporate mechanisms—such as the Repository Pattern—to abstract data 
  access and facilitate the integration and enhancement of public aggregated datasets.
- It must support data augmentation or synthesis processes to derive finer details from available
  datasets without violating data ownership or accessibility constraints.
- This approach ensures that the system remains flexible and sustainable, capable of meeting
  analytical requirements without reliance on proprietary data sources.

## Decision

We will implement the **Repository Pattern** to abstract data access within our system. This approach involves:

- **Abstracting Data Sources:** Creating a repository interface that the business logic interacts with, unaware of the underlying data source specifics.
- **Multiple Repository Implementations:** Developing different repository implementations for each dataset type (fine-grained and aggregated).
- **Dependency Injection:** Injecting the appropriate repository into the business logic at runtime, allowing for easy substitution and testing.

## References

- **Design Patterns:** "Patterns of Enterprise Application Architecture" by Martin Fowler.
- **Repository Pattern Overview:** [2: Repository Pattern](https://www.cosmicpython.com/book/chapter_02_repository.html)

---

By adopting the Repository Pattern, we create a flexible and secure architecture that meets our need to handle
both proprietary fine-grained datasets and public aggregated datasets effectively. This decision aligns with
our goals of maintaining data privacy, enhancing system flexibility, and promoting clean separation of concerns
within our software design.







