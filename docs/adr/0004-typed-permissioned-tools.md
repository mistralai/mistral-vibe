# 0004 Typed Permissioned Tools

## Decision

Tool arguments, results and failures have typed contracts. Every side effect
honors declared permissions and the Runtime approval path. Existing `BaseTool`
implementations are reusable; that base class is not required for every tool.

Shell policy analyzes nested execution and shell-native paths, including
Windows MSYS paths. Allowlisted readers still require approval for operands
outside the workspace.

One public effect spans invocation, approval, output and completion. Arbitrary
tools use generic projection; bounded semantic categories select rich renderers.
A closed built-in mapping stays in one adapter and preserves unknown tools.

## Rationale

Consistent permissions and result contracts let tools share execution and
presentation mechanisms without requiring per-tool RPCs or client dispatch.

## Agent Guidance

- Bound output for model context, logs and rendering.
- Keep permission resolution close to the protected operation.
- Test nested commands and platform path forms when changing shell policy.
- Add a presentation category only when it needs a distinct renderer.

## Flag To User When

- A tool bypasses approval or a permission resolver's decision.
- Ordinary tool invocation requires a special RPC or client execution path.
