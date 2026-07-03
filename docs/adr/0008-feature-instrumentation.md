# 0008 Feature Instrumentation

## Decision

Every feature must ship with analytics instrumentation. Telemetry is not optional or a follow-up — it is part of the feature work.

Before creating new events, search the event registry (`datalake-dbt/event_registry/<service>.yml`) and neighboring services for existing events that already capture the same or similar user action. Extend an existing event with new properties rather than creating a parallel one. Only create a new event when nothing existing fits.

Every event must carry the standardized metadata block (`properties.metadata`).

## Rationale

Features without telemetry are invisible to product and data teams. Fragmenting events across duplicates makes downstream queries unreliable and increases maintenance cost. Reusing existing events keeps the datalake consistent and composable.

## Agent Guidance

- When implementing any feature, use the `instrument-feature-analytics` skill to plan the telemetry.
- Search existing events broadly before defining new ones.
- Verify instrumentation locally (`DEBUG_LEVEL=1 uv run vibe`), then in staging (`logs_events_staging`), then in production (`logs_events`).

## Flag To User When

- A feature is being implemented without any telemetry plan.
- A new event duplicates or overlaps with an existing one in the registry.
- An event is missing the standardized metadata block.
