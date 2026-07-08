---
name: write-vibe-tests
description: Write or refactor Mistral Vibe tests with proper decoupling. Use when adding behavior coverage, testing ports/adapters, replacing brittle mocks, creating fakes, adding characterization tests before refactors, or changing tests under tests/ for vibe/core, vibe/cli, vibe/acp, tools, config, sessions, skills, hooks, MCP, or setup.
metadata:
  display-name: Write Vibe Tests
  short-description: Write decoupled behavior tests for Vibe
  default-prompt: Use $write-vibe-tests to add or refactor Mistral Vibe tests around behavior and stable boundaries.
---

# Write Vibe Tests

Use this skill to make Vibe architecture testable, not just well-shaped. Tests should protect observable behavior while allowing internals to move.

## Core Principle

Test behavior through stable boundaries. Do not couple tests to private methods, internal call choreography, or temporary structure.

If a refactor changes no observable behavior but breaks many tests, the tests are probably coupled to implementation details.

## Vibe Test Boundaries

| Code area | Preferred test boundary |
| --- | --- |
| Core use cases and services | Public function/class API, typed events, model outputs, persisted state |
| Tools | `BaseTool.invoke`/`run`, typed args/results, permission behavior, user-facing errors |
| LLM/backend orchestration | `AgentLoop` events and fake backend outputs |
| Config | Pydantic model validation, layer merge outputs, migration results |
| Sessions | Saved JSONL/metadata shape, resume/loader behavior, migration behavior |
| CLI widgets | Textual snapshots, posted messages, rendered user-visible state |
| ACP | ACP session updates and protocol-facing content, not core internals |

## Prefer Fakes Over Mocks

Prefer in-memory implementations and fake adapters that implement real contracts.

- Put reusable test doubles in `tests/stubs/` and name them `Fake*`.
- Make fakes small and behavior-oriented.
- Mock only at hard process, network, time, or third-party boundaries when a fake would be more complex than the behavior under test.
- Avoid assertions like "method X was called with Y" unless the call itself is the observable contract.

## Legacy Or Refactor Workflow

When code is hard to test:

1. Find the smallest seam: function boundary, constructor dependency, protocol/port, wrapper, composition root, feature flag, or module boundary.
2. Add characterization tests through the nearest public entry point.
3. Capture current observable behavior, even if awkward.
4. Refactor behind the seam in small steps.
5. Replace broad characterization checks with clearer behavior/spec tests as the design improves.

Prefer "make it testable" refactors first: isolate I/O, extract pure functions, introduce ports/adapters where useful, or move construction out of business logic.

## Test Shape

- Use descriptive test names; do not add test docstrings.
- Arrange, act, and assert clearly, but optimize for readability over ceremony.
- Keep tests deterministic, fast, and explicit about failure.
- Use autouse fixtures from `tests/conftest.py` for config/home/working-directory isolation.
- Mark async tests with `@pytest.mark.asyncio`.
- Mock outbound HTTP with `respx`.
- Use the narrowest relevant test first, then broaden when shared contracts are touched.

## Avoid

- Testing private methods as the primary coverage for behavior.
- Asserting intermediate internal state when user-visible output, emitted events, files, or return values can be asserted.
- Building mocks that mirror the implementation.
- Adding abstractions only to satisfy a test.
- Writing tests that require a specific internal file split or call order when the domain behavior is unchanged.

## Verification

After Python test/code changes, run:

```bash
uv run ruff format .
uv run ruff check --fix .
```

Then run the targeted tests:

```bash
uv run pytest <test-path-or-node-id>
```

Run `uv run pyright` when signatures, models, protocols, or shared contracts changed.
