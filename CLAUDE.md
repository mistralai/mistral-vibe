
## Specification

When we talk about a "specification" or "spec", we mean:
- Persona Use Cases in Cockburn's Fully Dressed format (Primary Actor, Trigger, Main Success Scenario, Extensions, Postconditions) at User Goal level, with Business Rules (BR-IDs)
- System Use Cases for each technical interface (API endpoint, CLI command, event, file format): input/validation, processing, output/status codes, error responses
- Activity Diagrams for all flows (not just the happy path)
- Acceptance criteria in Gherkin format (Given/When/Then)
- Individual requirements in EARS syntax where applicable (When/While/If/Shall)
- Supplementary Specifications as needed: Entity Model, State Machines, Interface Contracts, Validation Rules

## Requirements Discovery

Clarify requirements using the Socratic Method:
- Ask at most 3 questions at a time, challenge assumptions
- Use MECE to ensure questions cover all areas without overlap
- Keep asking until you fully understand the requirements

Frame the scope before writing it down:
- Impact Mapping connects deliverables to business goals and actors — so you build what moves a goal, not just what was asked.
- User Story Mapping lays stories along the user's journey and exposes a coherent first slice.

Document the result as a PRD (problem, goals, personas, success criteria, scope).

## Architecture Documentation

Architecture documentation follows arc42. Scaffold the arc42 "with-help" template into the project's `src/docs/` via docToolchain `downloadTemplate` rather than restating chapter structure here — each chapter's help text is its structural spec, which the process fills and then replaces.

Every context, building-block and runtime chapter carries at least one diagram. Diagrams are PlantUML, not Mermaid; building blocks use C4 via PlantUML's bundled C4-PlantUML standard library — the `!include <C4/...>` stdlib form (angle brackets), never the remote `https://` URL and never vendored file copies. Not generic boxes.

Decisions are ADRs (Nygard) with a 3-point Pugh Matrix (-1/0/+1). When the rationale is unconfirmed, ADR Status is "Accepted (inferred)" and Pugh cells needing team judgment are marked `?` rather than guessed. Each ADR's Consequences name the risks the decision creates, referencing the Chapter 11 risk IDs (R-NNN); a decision that creates a risk not yet in Chapter 11 either adds it there or records the consequence as explicitly accepted without a tracked risk. Conversely, Chapter 8 concepts back-reference the ADR that decided them.

Cross-section traceability — arc42 templates do not enforce these, so the contract does:
- Every Chapter 1.2 quality goal maps to a named approach in Chapter 4.
- The external systems in Chapter 3 (context) and the Chapter 5 Level-1 building-block view are the same set — one system boundary in both.
- Every Chapter 5 building block appears in at least one Chapter 6 runtime scenario; Chapter 6 includes at least one error/recovery scenario, not only the happy path.
- Chapter 9 carries an in-document ADR index (ADR | Title | Status), even when the ADRs live in a separate register.
- Each Chapter 5 building block states responsibility, interface, and source location.

Chapter 1.2 lists only the top 3-5 quality goals — the ones that drive architecture decisions. Chapter 10 may elaborate further quality characteristics beyond those top goals; that is correct arc42, not a defect. The Chapter 10 quality tree marks each characteristic as either concretising a Chapter 1.2 top goal or as a derived quality requirement, and each Chapter 10 quality scenario cross-links back to the Chapter 1.2 goal it concretises (or is marked "derived"). Each Chapter 10 scenario is written in the six-part quality attribute scenario form (Source, Stimulus, Artifact, Environment, Response, Response Measure); the Response Measure carries a literal figure, so the requirement is testable rather than an adjective.

Chapter 11 separates Risks from Technical Debt into two subsections. Each Risk carries probability, impact, a derived priority, and a mitigation/action cross-referencing an existing mitigation in Chapter 8 or a quality scenario where one exists; risks are ordered by priority. Each Technical Debt item references the specific Chapter 5 building block it burdens.

## Crosscutting Concepts

arc42 leaves Chapter 8 open. We require five baseline crosscutting concepts, in this order:

- 8.1 Threat Model — STRIDE; threats get IDs (T-001…).
- 8.2 Security — every mitigation references the T-IDs it closes.
- 8.3 Test — testing pyramid; tests trace to Use Cases and Business Rules.
- 8.4 Observability — logs, metrics, traces, audit trails.
- 8.5 Error Handling — retry, circuit breaker, fallback, recovery.

Add further Chapter 8.x concepts (persistence, i18n, accessibility, configuration, performance) only when the system actually has that concern.

## Layer Boundaries

At every layer boundary:
- Expose only well-defined DTOs and contracts — never domain entities
- Use explicit mapping at every seam
- Apply Anti-Corruption Layers when integrating external systems
- Dependency direction points inward (DIP)

## Backlog Management

Create EPICs and User Stories as GitHub issues from the specification.
- User Stories follow INVEST criteria (Independent, Negotiable, Valuable, Estimable, Small, Testable)
- Prioritize with MoSCoW (Must/Should/Could/Won't)
- Mark dependencies between issues
- Groom the backlog regularly as the project evolves

## Vertical Slicing

Build the first increment as a walking skeleton: a deployable end-to-end slice that wires every architectural layer together and does almost nothing else.

Grow the system as thin vertical slices — each slice cuts through all layers and delivers one small piece of user value. Slices are tracer bullets: kept and refined, never thrown away.

When a technical unknown blocks a slice, run a spike solution first — a timeboxed, throwaway experiment that removes the risk. Spike code is discarded; only its lesson carries into the slice.

## Implement Next

For each issue:
- Create a feature branch for the EPIC
- Select next issue from backlog (respect dependencies)
- Analyze and document analysis as a comment on the issue
- Implement using TDD (London or Chicago School as appropriate)
- Each test references its Use Case ID for traceability
- Commit with Conventional Commits, reference issue number
- Check if spec or architecture docs need updating
- When EPIC is complete, create a Pull Request

## Refactoring

Refactoring targets are named code smells, not a vague urge to "clean up".

For any refactoring that does not complete in one step, use the Mikado Method: attempt the change, note what breaks, revert, and do the prerequisites first — never leave the build broken while you dig.

Refactoring commits change structure only. Behaviour changes go in separate commits, and the test suite stays green at every commit.

## Code Quality

Our code follows:
- SOLID principles
- DRY, KISS
- Ubiquitous Language from Domain-Driven Design (same terms in code as in the specification)

## Quality Review

Quality assurance follows three layers:
- Code review using Fagan Inspection (structured, systematic, with defined phases)
- Security review based on OWASP Top 10
- Architecture review using ATAM (scenario-based tradeoff analysis against quality goals)
- Use a different AI model or fresh session for reviews to avoid blind spots

## Docs-as-Code

Documentation follows Docs-as-Code according to Ralf D. Müller:
- AsciiDoc as format, PlantUML for inline diagrams, built by docToolchain
- Version-controlled, peer-reviewed, and built automatically
- Plain English according to Strunk & White (or Gutes Deutsch nach Wolf Schneider)
- Projects following this contract include the `dtcw` wrapper and `docToolchainConfig.groovy` so PlantUML / AsciiDoc actually render.

## Socratic Code Theory Recovery

Recover a program's "theory" (Naur 1985) from source code through recursive question refinement.

- Start with 5 root questions: Q1 Problem/Users, Q2 Specification, Q3 Architecture, Q4 Quality Goals, Q5 Risks.

- The second level of the tree is FIXED, not free. Every run emits exactly these nodes, in this order, even when a node's only leaf is [OPEN] or [ANSWERED: not applicable]:
  - Q1.1-Q1.6: product identity, primary users, channels, why-built, success metrics, segment priority
  - Q2.1-Q2.6: actors, use-case catalog, per-interface system specs, data/entity model, acceptance criteria, cross-cutting business rules
  - Q3.1-Q3.12: the twelve arc42 chapters, in arc42 order
  - Q4.1-Q4.8: the eight ISO/IEC 25010 characteristics; plus Q4.9: which characteristic has priority
  - Q5.1-Q5.5: technical debt, security risks, operational risks, dependency/supply-chain risks, scaling/performance risks

- Below the fixed second level, decompose adaptively and code-driven; a node is a leaf only when it can be answered from one specific file:line evidence (a directory is too coarse — decompose further) or definitively marked [OPEN]. Depth tracks code density: a small bounded context yields a shallow tree, a large one a deep tree, capped at four levels below a fixed node. Depth varies between runs — expected.

- Q-IDs are stable: Q3.7 is always Deployment View, in every run, so trees from different runs can be diffed node-by-node.

- Each leaf is [ANSWERED] (with file:line evidence) or [OPEN] (with Category, Ask role, and why it is unanswerable from code).

- Quality is not wholly team knowledge. Derive quality scenarios for the Q4 branch and arc42 Chapter 10 from measurable code behaviour — literal thresholds, timeouts, budgets, the threat catalogue and test concept from Q3.8 — as [ANSWERED] with file:line; never invent target numbers. Only the quality-goal ranking (Q4.9) is [OPEN]. arc42 Chapter 10 carries the derivable scenarios, never just an [OPEN] pointer. Chapter 1.2 names only the top 3-5 quality goals; Chapter 10 covers all eight characteristics — mark each Chapter 10 entry as concretising a Chapter 1.2 top goal or as derived.

- Open Questions are the handoff document: always emit one section per role (Product Owner, Architect, Developer, Domain Expert, Operations), even when a section is empty ("No open questions for this role").

- Two-phase workflow: Phase 1 builds the tree; the team answers the Open Questions; Phase 2 synthesizes documentation from the answered tree.

## Concise Response (TLDR)

Responses lead with the conclusion first (BLUF). Keep to essential points. No filler, no preamble. Use short sentences, active voice, and no unnecessary words (Strunk & White).

## Simple Explanation (ELI5)

Explain complex concepts using simple language and everyday analogies. When the explanation feels hard to write, that reveals gaps in understanding — study those areas first (Feynman Technique).

## Writing Style

Writing follows Gutes Deutsch nach Wolf Schneider (or Plain English according to Strunk & White).

Additionally:
- Technical terms stay in English (LLM, Prompt, Token, Spec, etc.)
- Address the reader directly, use first person sparingly but deliberately
- Use analogies to human thinking to explain technical concepts
- One thought per paragraph (5-8 sentences is fine)
- Section headings are statements, not topic announcements
- First sentence says what the paragraph is about
- Show code and prompts, don't just claim things work
- Conclusions make a clear statement — never end with 'it remains exciting'

---
Generated from https://llm-coding.github.io/Semantic-Anchors/#/contracts

## Docs-as-Code
- docToolchain reads its sources from src/docs/. Write all
  documentation there.
- Fill the arc42 template at src/docs/arc42/; do not create a
  standalone arc42 file. Write each chapter into its section
  file, set the document title to the system name, and remove
  the generic "About arc42" help chapter.
- PRD and specification go directly under src/docs/.
- Cross-references must resolve inside src/docs/; do not point
  at the Question Tree or the repo-root docs/ folder.
