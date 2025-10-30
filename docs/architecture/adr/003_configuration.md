 # Architecture Decision Record: Use Pydantic for Configuration Management

```
Status: Proposed
Date: 2021-11-15
```
## Context

Our project requires a centralized configuration system. The key requirements are:

- **Centralization**: A single point of configuration to avoid scattering settings across the codebase.
- **Flexibility**: Ability to configure the application using environment variables and/or dot
  files (e.g., `.env` files).
- **Validation**: Ensure that configuration data is valid and adheres to expected types
  and constraints.

We have evaluated several options for configuration management:

- **python-dotenv**: Lightweight but lacks built-in validation and type enforcement.
- **Environs** and **python-decouple**: Offer type casting and basic validation but introduce
  additional dependencies.
- **Dynaconf**: Feature-rich but adds a heavier dependency and complexity.
- **Custom Solutions**: Require additional development effort for features like validation
  and type casting.

**Pydantic** might also being used for handling data repositories that store data in JSON format.

## Decision

Use **Pydantic's `BaseSettings`** class for configuration management in our project.

- **Definition of Settings**: Create configuration classes that inherit from `BaseSettings`.
  These classes will define all necessary configuration variables with type annotations and 
  default values if applicable.
- **Environment Variables and Dot Files**: Pydantic `BaseSettings` supports loading configurations
  from environment variables and can be easily extended to read from dot files.
- **Validation and Parsing**: Leverage Pydantic's validation to ensure all configuration
  data is correct at startup, reducing runtime errors due to misconfigurations.
- **Consistency**: Using Pydantic for both data models and configuration keeps the
  codebase consistent and reduces the learning curve for developers.

## Consequences

### Pros

- **Robust Validation**: Strong data validation ensures configurations are correct and
  reduces bugs related to invalid settings.
- **Type Enforcement**: Type annotations improve code clarity and enable IDE features
  like autocomplete and type checking.
- **Unified Dependency**: Since Pydantic is already a project dependency, we avoid adding
  new packages, reducing potential dependency conflicts.
- **Flexible Configuration Sources**: Supports loading from environment variables,
  dot files, and even complex nested configurations.
- **Ease of Use**: Declarative configuration models are easy to read and maintain.

### Cons

- **Learning Curve**: Developers need to be familiar with Pydantic's model syntax and
  validation mechanisms.
- **Dependency Weight**: While acceptable in our case, Pydantic can be considered
  heavy for projects that do not already use it.

 ## Alternatives

- **Continuing with python-dotenv**: Rejected due to lack of validation and type enforcement.
- **Adopting Environs or python-decouple**: Not chosen to avoid adding new dependencies
  when Pydantic already fulfills our requirements.
- **Using Dynaconf**: Deemed too heavy and complex for our needs, especially given the
  existing use of Pydantic.
- **Building a Custom Solution**: Unnecessary given that Pydantic provides the needed
  features out of the box.

## References

- **Pydantic Documentation**: [Settings Management](https://docs.pydantic.dev/latest/usage/pydantic_settings/)



