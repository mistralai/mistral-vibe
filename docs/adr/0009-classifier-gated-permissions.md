# 0009 Classifier Gated Permissions

## Decision

Vibe gains an `auto` permission mode. Tool calls that would otherwise prompt the user are instead routed to a separate, cheap classifier model (`PermissionClassifier`) that returns allow/block against four tiers of prose rules — `hard_deny`, `soft_deny`, `allow`, and `environment` — configured through `AutoModeConfig`.

This is a second gate layered on top of the `PermissionContext` contract from 0004 Typed Permissioned Tools. It does not replace or fork tool permission semantics: explicit ALWAYS/NEVER permissions and session-approved rules still resolve before the classifier is ever consulted.

## Rationale

The gap between `accept-edits` (prompts on every shell command) and `auto-approve` (prompts on nothing) is a cliff. Users facing prompt fatigue jump straight to `auto-approve` and stop reviewing, so the mode meant as a last resort becomes the default. A classifier-gated middle lets routine work run uninterrupted while genuinely risky actions still stop. Running it on a small, fast model keeps per-call cost low enough to classify every shell command rather than only the ones that miss existing rules.

## Agent Guidance

- The classifier must never see tool results. It receives only system, user, and assistant messages. Tool output is attacker-controlled (file contents, command stdout, fetched pages); letting it reach the classifier would let a hostile file argue its own way past the gate. Any change that widens the classifier's input is a security regression.
- A malformed, timed-out, or unavailable classifier response is never an allow — it falls through to a human prompt.
- The classifier is a mitigation, not a guarantee. Do not describe `auto` mode as safe, and do not extend it to replace review on sensitive operations.
- Rule lists in `AutoModeConfig` are additive over the built-in defaults in `permission_classifier.md`; config cannot delete a default rule.
- The classifier is created lazily. A session that never enters `auto` mode must never construct a classifier backend (0001 startup budget).
- A blocked action returns the classifier's reason as tool feedback so the model can choose an alternative, rather than a generic denial.
- `auto` mode pauses after repeated blocks and falls back to normal prompting, so a mis-tuned rule set degrades into prompts rather than an infinite denial loop.

## Flag To User When

- A change would let tool results, or any other model-controlled content, reach the classifier.
- A caller wants to treat an unavailable or failed classifier call as an allow.
- A new surface (ACP, programmatic, subagents) needs to bypass the gate rather than adopt it.
- Someone proposes `auto` mode as a replacement for `bypassPermissions`-style isolation, or as a safety guarantee.
