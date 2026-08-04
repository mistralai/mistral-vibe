# 0004 Typed Permissioned Tools

## Decision

Tools are typed, permissioned ports into side effects. A tool has Pydantic args, result, config, and state types, and runs through `BaseTool`.

Permission policy is part of the tool contract. Tools that touch files, processes, network, or external services must declare and honor permissions consistently. Surface-specific tool behavior should adapt the same core contract rather than fork the domain semantics.

Shell permission analysis covers semantic execution rather than only top-level
command text. It recursively inspects nested command constructs and normalizes
shell-native paths before workspace-boundary checks, including MSYS drive paths
on Windows. Auto-allowlisted readers still require outside-directory approval
when their path arguments leave the workspace.

One public app-server effect entry represents the tool call, streaming output,
approval blocking, result, duration, and terminal state. Public effect kinds are
semantic presentation categories, not a registry of tool names. Arbitrary MCP,
connector, custom, and future tools use the generic effect projection without
adding app-server dispatch code.

Tools that support client-hosted filesystem or terminal operations use
`ToolIOPort`, and the app server sends typed `clientTool/*` requests. The server
still owns tool validation, permissions, lifecycle, public effects, and
model-visible results. Tools without that port keep their server-side execution
semantics.

## Rationale

Tools are the highest-risk extension point. Typed contracts make LLM calls, validation, UI display, session logging, ACP translation, and tests agree on one shape. Permission handling limits blast radius.

## Agent Guidance

- Implement new tools under the existing `BaseTool` pattern with typed args/results/config/state.
- Raise `ToolError` for user-facing failures and `ToolPermissionError` for authorization failures.
- Keep permission resolution close to the tool behavior it protects.
- Keep path-inspection coverage at least as broad as shell reader allowlists,
  and test nested commands and platform-specific path forms.
- Prefer core tool semantics in `vibe/core/tools`; put ACP or UI adaptation in surface-specific layers.
- Keep tool output bounded and safe for LLM context, logs, and session transcripts.
- Extend the tool presentation contract for a genuinely new semantic renderer;
  do not add switches on individual tool names in the app server or TUI.
- Project approvals as typed callback entries related to the same effect. Do
  not install UI callbacks on tools or the agent loop.

## Flag To User When

- A tool bypasses the permission model because it is easier for one caller.
- A tool returns ad-hoc dictionaries or strings where a typed result should exist.
- UI or ACP behavior would require changing the core tool contract in a surface-specific way.
- A new tool requires app-server registration only to make its ordinary call or
  result visible.
