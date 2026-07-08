# 0004 Typed Permissioned Tools

## Decision

Tools are typed, permissioned ports into side effects. A tool has Pydantic args, result, config, and state types, and runs through `BaseTool`.

Permission policy is part of the tool contract. Tools that touch files, processes, network, or external services must declare and honor permissions consistently. Surface-specific tool behavior should adapt the same core contract rather than fork the domain semantics.

## Rationale

Tools are the highest-risk extension point. Typed contracts make LLM calls, validation, UI display, session logging, ACP translation, and tests agree on one shape. Permission handling limits blast radius.

## Agent Guidance

- Implement new tools under the existing `BaseTool` pattern with typed args/results/config/state.
- Raise `ToolError` for user-facing failures and `ToolPermissionError` for authorization failures.
- Keep permission resolution close to the tool behavior it protects.
- Prefer core tool semantics in `vibe/core/tools`; put ACP or UI adaptation in surface-specific layers.
- Keep tool output bounded and safe for LLM context, logs, and session transcripts.

## Flag To User When

- A tool bypasses the permission model because it is easier for one caller.
- A tool returns ad-hoc dictionaries or strings where a typed result should exist.
- UI or ACP behavior would require changing the core tool contract in a surface-specific way.
