---
name: instrument-feature-analytics
description: Plan analytics instrumentation for a feature. Use when implementing any feature — every feature must ship with telemetry. Decides which events and properties the metrics need (funnels, adoption, status flows), checks compatibility with the existing datalake and event registry, and walks through local, staging, and production verification.
metadata:
  author: datascience-team
  version: "1.0"
  display-name: Instrument Feature Analytics
  short-description: Plan and verify analytics instrumentation for features
  default-prompt: Use $instrument-feature-analytics to plan the telemetry for a feature before writing tracking code.
user-invocable: true
---

# Instrument a Feature for Analytics

Answer the tracking questions a software engineer would normally take to a data scientist or data engineer. When they bring one here, answer it the way a data person would, and proactively cover what they didn't think to ask.

The software engineer already knows how to emit an event technically. The hard part — and the reason they'd normally ask a data person — is deciding *which* events and properties matter for the metrics, and making sure they fit what already exists so the data is actually usable. Events land in `mistral-data.lake_eu.logs_events`.

Use the steps below as the full picture to reason from, not a rigid script: if the software engineer arrives with a specific question, answer that first, then pull in whatever other steps are relevant. If they arrive with "I'm adding feature X, help me track it", walk it in order.

## Process

### 1. Understand the feature and the metrics behind it

Ask the software engineer a few questions before anything else:

- **What is the feature?** (a new onboarding step, a way to discover a feature, a status/lifecycle change, an action users repeat…)
- **What will product and data want to measure about it?** Adoption? A funnel and where people drop off? How often a status changes? Whether a surface gets noticed?

Then translate those questions into the events and properties that would answer them. Common patterns:

- **Funnel (e.g. onboarding):** one event per step (`started`, `step_completed`, `completed`), with a property identifying the step and its order, plus a shared id to stitch the steps of one user together.
- **Discoverability:** distinguish *seen* from *acted on* — an impression event when the thing is shown and an interaction event when the user engages, so you can compute a noticed→used rate.
- **Status / lifecycle updates:** one event per transition carrying `from_status` and `to_status` (and what triggered it), so changes can be reconstructed over time.
- **Repeated action / engagement:** one event per occurrence with enough context (what, where) to segment it.

The goal of this step is coverage: make sure every question the software engineer named maps to at least one event + the properties needed to slice it. Naming comes after.

### 2. Check what already exists — reuse first, create second

Before defining any new event, search for existing events that already capture the same (or similar) user action. A new event that duplicates an existing one fragments the data and makes downstream queries harder.

Check `datalake-dbt` on the software engineer's behalf (they never have to open that repo):

- Read the event registry (`datalake-dbt/event_registry/<service>.yml`) for the software engineer's service **and for neighboring services that touch the same domain**. Grep broadly — the event you need may already exist under a slightly different name or in a different service's namespace.
- If an existing event covers the action but is missing a property, **extend it** (add the property) rather than creating a parallel event.
- If no existing event fits, create a new one — but **reuse property names and types** from related events so the new data composes with the old instead of fragmenting it.
- Keep one stable type per property and avoid nesting deeper than two levels — these are what let the data be modeled cleanly and stay queryable over time.

Flag anything that would force an awkward downstream change or that won't age well, and propose the compatible shape instead.

### 3. Naming

Names are a data-consistency concern, so keep them conventional: `<prefix>.<noun>.<verb>`, snake_case, dot-namespaced, at least two segments — the prefix maps to the service (e.g. `vibe.session_initialized`, `harmattan.completion.done`). Reuse the wording of existing similar events rather than inventing a parallel vocabulary.

### 4. Standardized metadata

Every event must populate the standardized metadata block (`properties.metadata`): `call_source`, `call_type`, `session_id`... Look at existing events in the registry to see which metadata fields are used for the service and carry them over.

### 5. Cover every surface

The same user action on web, cli, mobile, and api must emit the **same event with the same properties**. List the surfaces in step 1 and confirm each one emits.

### 6. PII

If any property carries personally identifiable data (raw user content, email, file path, names), flag it explicitly to the data team before shipping — it changes how the field must be stored and queried. Prefer sending an ID over the raw value.

### 7. Verify locally

Run the app with `DEBUG_LEVEL=1 uv run vibe` — every telemetry event is logged right before the API call. If the log line appears, the event will reach the pipeline. Use this to confirm the event fires with the expected properties before deploying.

### 8. Verify in staging

After deploying to the staging environment, trigger the action a few times per surface, then run the verification queries below against `mistral-data.lake_eu.logs_events_staging`. Confirm events fire with the right properties and values before promoting to production.

### 9. Verify in production

After shipping to production, repeat the same manual tests and run the queries against `mistral-data.lake_eu.logs_events`.

### Verification queries

Run these in Metabase or via the BigQuery connector. Use `logs_events_staging` when verifying in staging, `logs_events` in production.

**Volumetry + completeness of every property** — fill in your event name(s):

```sql
WITH events AS (
  SELECT event, properties
  FROM `mistral-data.lake_eu.logs_events`
  WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    AND event IN UNNEST(['vibe.session_initialized'])   -- <- your event name(s)
),
totals AS (
  SELECT event, COUNT(*) AS total_events
  FROM events
  GROUP BY event
),
property_presence AS (
  SELECT event, key AS property, COUNT(*) AS present
  FROM events, UNNEST(JSON_KEYS(properties, 2)) AS key   -- depth 2 = includes metadata.*
  GROUP BY event, key
)
SELECT
  t.event,
  t.total_events,
  p.property,
  p.present,
  ROUND(p.present / t.total_events, 3) AS completeness   -- 1.0 = present on every event
FROM totals t
LEFT JOIN property_presence p USING (event)
ORDER BY t.event, completeness DESC;
```

Read it as: `total_events` non-zero and roughly matching your manual test count means the event reaches the lake. Every property you meant to always send should read `1.0`; below that, some code path or surface isn't sending it. A property you expected but don't see at all never fired.

**Distinct values of each property** — this catches what completeness misses (a field always present but always the same wrong constant, an enum sending an unexpected value, an ID coming through empty). List the properties worth inspecting (categorical / enum-like ones; skip high-cardinality IDs):

```sql
WITH events AS (
  SELECT properties
  FROM `mistral-data.lake_eu.logs_events`
  WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    AND event IN UNNEST(['vibe.session_initialized'])   -- <- your event name(s)
)
-- one block per property you want to inspect; add/remove blocks as needed:
SELECT 'surface'      AS property, JSON_VALUE(properties, '$.surface')      AS value, COUNT(*) AS n FROM events GROUP BY value
UNION ALL
SELECT 'warm_claimed' AS property, JSON_VALUE(properties, '$.warm_claimed') AS value, COUNT(*) AS n FROM events GROUP BY value
ORDER BY property, n DESC;
```

Each row is a value and how often it appeared. Check the set of values is what you expect (e.g. `surface` shows `web` and `cli` and nothing weird) and that nothing is unexpectedly `NULL` or empty.

To confirm per-surface coverage from step 5, break volumetry down by surface:

```sql
SELECT event, JSON_VALUE(properties, '$.surface') AS surface, COUNT(*) AS n
FROM `mistral-data.lake_eu.logs_events`
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
  AND event IN UNNEST(['vibe.session_initialized'])
GROUP BY event, surface
ORDER BY n DESC;
```

Every surface you expected should appear with a non-zero count.
