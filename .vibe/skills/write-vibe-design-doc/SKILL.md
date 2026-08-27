---
name: write-vibe-design-doc
description: Create or update implementation-ready design proposals for Mistral Vibe under docs/design, including a bounded design-tree decision review before drafting. Use before implementing a substantial Vibe feature, migration, protocol or API change, runtime or persistence change, cross-component workflow, compatibility plan, or other work whose ownership, behavior, failure semantics, rollout, and validation need review.
metadata:
  display-name: Write Vibe Design Doc
  short-description: Create evidence-backed Vibe design proposals
  default-prompt: Use $write-vibe-design-doc to turn a Vibe feature or migration idea into an implementation-ready design proposal.
---

# Write Vibe Design Doc

Produce a decision artifact that a reviewer can use to evaluate the proposal and
an implementer can use as a completion checklist. Base it on product intent and
the real code path, not on a plausible architecture invented from file names.

## Design Doc Versus ADR

- Write a design doc for proposed work, implementation choices, migrations,
  alternatives, rollout, and verification.
- Use `write-vibe-adr` for a concise, durable architecture rule that future
  changes must follow. A design may require a new or updated ADR, but it does
  not replace one.
- Unless the user also asks for implementation, stop after the reviewed design
  document. Do not make product-code changes implicitly.

## Workflow

### 1. Establish scope and authority

1. Identify the user outcome, affected surfaces, requested deliverable, and
   destination. Default new proposals to `docs/design/<descriptive-slug>.md`.
   Honor response-only or alternate-destination requests without creating a
   repository artifact.
2. Collect named product specifications, issues, accepted decisions, and user
   corrections. Treat an explicitly named product source of truth as the target
   authority; use current code and ADRs to explain the starting point.
3. Read `README.md`, `AGENTS.md`, the matching ADRs, and one or two nearby
   design documents. Read the nearest `AGENTS.md` before examining a sibling
   project.
4. If the target direction conflicts with an ADR, flag the conflict and include
   the required ADR follow-up. Do not silently dilute either source.

When updating an existing proposal, preserve accepted decisions that the user
has not changed. Apply each correction throughout the document instead of
appending a new section that contradicts old routes, diagrams, ownership,
failure semantics, or acceptance checks.

Ask only about a missing decision that would materially change the design. For
example, clarify whether a command runs before startup or inside an active
session when that choice changes which failures it can diagnose. Otherwise,
state a bounded assumption and continue.

### 2. Trace the current system

Trace the production path end to end across every affected boundary, such as:

```text
CLI / Textual / ACP / client -> app server -> owning port -> runtime/backend
                            -> effects/persistence -> public projection
```

- Identify the owner of each behavior, state transition, configuration value,
  and persisted record.
- Follow construction and selection paths as well as the method being changed.
- Link to precise repository files or authoritative external specifications.
- Label conclusions as source-confirmed, test-confirmed, or runtime-verified.
  Do not present a source-only protocol concern as a reproduced runtime bug.
- For each material observation, state the constraint or design decision it
  causes. Do not collect evidence that has no effect on the proposal.
- Resolve related repositories only through locations documented in
  `AGENTS.md`; ask for the path when a documented sibling is absent.

### 3. Lock the problem and constraints

Write the background, problem, goals, non-goals, terminology, and requirements
before proposing modules. Make user-visible behavior explicit, including
affordances, defaults, interrupts, retries, cancellation, and degraded states.
Separate current limitations from intentional target behavior.

### 4. Design one coherent system

- Assign one clear owner to every concern. Dependencies may point toward the
  owner; reverse callbacks or duplicated policy need explicit justification.
- Prefer target-shaped contracts and direct ownership. Add a compatibility
  layer only with a bounded migration need, named owner, and removal condition.
- Carry every accepted decision through component boundaries, APIs and routes,
  data models, state machines, persistence/recovery, concurrency, failure
  semantics, compatibility, rollout, and tests.
- Specify what happens before and after partial failure. Include idempotency,
  retry, cancellation, cleanup, and restart behavior where applicable.
- Include an edge case when it changes the design, requires separate behavior,
  or prevents serious security, authorization, data-loss, or repeated-effect
  risk. Group cases when one rule determines the same safe response. Omit
  speculative, unlikely, low-consequence cases already covered by that rule.
- Include security, privacy, or observability only when the feature introduces
  a relevant behavior, risk, or operational need. Name the feature-specific
  concern and response; do not add generic logging, metrics, or inherited
  controls.
- Use a Mermaid diagram only when it makes ownership, sequence, state, or
  migration materially clearer than prose or a small table.

### 5. Resolve material decisions before planning

Once the core design seems coherent, pause before writing the implementation
plan or drafting the document. Map the unresolved **material decisions** as a
**design tree**: every decision branches into the decisions that depend on it.
A decision is material when its answer changes user-visible behavior,
ownership, contracts, state, failure semantics, compatibility, rollout, or
acceptance criteria.

Work the tree in **rounds**. The **frontier** is every material decision whose
prerequisites are settled: the questions you can ask *now* without guessing at
answers you have not heard yet. Ask the whole frontier in one round, number each
question, and give your recommended answer. Then wait for the user's answers
before the next round.

Format each question like this:

```php-template
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Each round of answers reshapes the tree: settled decisions push the frontier
outward and unblock questions that depended on them. Recompute the frontier and
ask the next round. A question whose answer depends on another question still
open in this round belongs to a later round, not this one. Do not ask for
ceremonial agreement, facts already established by the source trace, or minor
implementation choices that do not materially change the design.

Finding *facts* is your job, never the user's. When a frontier question needs a
fact from the environment, dispatch a sub-agent to find it; do not ask the user
for anything you could look up yourself. Do not block on it: a running
exploration is an unsettled prerequisite, so only the questions downstream of
it wait for the sub-agent to report. Ask the rest of the frontier now. The
*decisions* are the user's: put each to them and wait.

The decision review is done when no unresolved material decision remains and
the user confirms shared understanding. Record non-material details as bounded
assumptions instead of extending the interview indefinitely. Do not write the
implementation plan or draft the design until this gate is complete.

### 6. Make implementation and validation executable

- Map implementation slices to concrete owning packages and files without
  pretending that exploratory file names are final.
- Prefer vertical slices that prove a user-visible path over disconnected
  layers of scaffolding.
- Include unit, contract, integration, end-to-end, migration, and platform
  checks in proportion to the risk.
- Test the production composition and selection path, not only a directly
  constructed implementation.
- Start the Validation Plan with a short, visible completion checklist covering
  implementation slice exit criteria, required validation, and rollout or
  documentation work. Refer to earlier sections instead of repeating details.
- Put the detailed validation matrix, commands, and expected results in a
  `<details>` block below that checklist. Focused passing tests and soft-failing
  CI do not satisfy the complete acceptance boundary.
- Keep reviewer decisions in the main design. Put exact types, schemas, method
  signatures, protocol mappings, file maps, and algorithms in collapsible
  sections or the appendix when they do not need independent approval.

### 7. Draft from the template

Read `DESIGN-DOC-TEMPLATE.md` from this skill directory before drafting. Adapt
the template to the proposal, but preserve its decision, failure, rollout, and
validation coverage. Remove optional or irrelevant sections rather than adding
generic content. Do not leave placeholders in the finished document.

Use Sections 1 through 7, Alternatives, and Rollout as the reviewer path. Keep
implementation-only material in the Implementation Plan, Validation Plan, or
Appendix without hiding decisions that require reviewer approval. For documents
around 500 lines, add a table of contents unless navigation is already clear.

Keep the document easy to scan:

- Use short, title-case headings in a sequential hierarchy, with a maximum
  depth of four by default. A heading labels a topic; the body makes the
  argument.
- Use bold sparingly. Do not decorate headings or bullets with emoji or Unicode
  styling.
- Use a table for mappings and comparisons, a diagram for relationships or
  sequences, a list for discrete items, and code for exact contracts. Use one
  only when it communicates more clearly than prose.
- State each rule once. Refer back to it instead of repeating it in the
  design, checklist, and validation plan.

Keep claims auditable:

- Link requirements to their source.
- Link current behavior to source files and tests.
- Prefer one authoritative source and relevant first-party evidence over a
  stack of secondary links. Use working links without tracking parameters.
- Mark proposed names and wire shapes as proposals rather than existing APIs.
- Use normative language for requirements and plain present tense for current
  behavior.
- If a material decision remains unresolved, return to step 5. Do not publish a
  finished design with unresolved blockers.

### 8. Run an adversarial implementation-readiness review

After drafting, dispatch an independent sub-agent with the document and this
task: "Assume you must implement this proposal using `/goal`. What is missing,
ambiguous, contradictory, or untestable?"

Give the reviewer the document and relevant source artifacts, not your intended
answer or prior conclusions. Reconcile every material finding. If a finding
exposes a new user decision, return to step 5 and wait for confirmation before
revising the design. Run at least one adversarial pass; repeat it only when the
resulting revisions materially change the proposal.

### 9. Review the document

Before handing off:

1. Check that every goal maps to a proposed mechanism and acceptance check.
2. Check that non-goals do not reappear as hidden implementation requirements.
3. Reconcile ownership across prose, tables, diagrams, APIs, and failure flows.
4. Verify that no unresolved material decision or implementation blocker
   remains.
5. Verify relative links, commands, terminology, and referenced symbols against
   the current checkout.
6. When a document was written, run `git diff --check -- <document-path>`.
   Skip file-only checks for a response-only draft.
7. Report separately what was source-confirmed, test-confirmed, and not
   runtime-verified.
8. Check that low-probability cases are present only when their consequence or
   distinct implementation behavior justifies reviewer attention.
