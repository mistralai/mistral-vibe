# 0009 Classifier Gated Permissions

## Decision

Vibe gains an `auto` permission mode. Tool calls that would otherwise prompt the user are first routed to a separate, cheap classifier model (`PermissionClassifier`) that returns allow/block against four tiers of prose rules — `hard_deny`, `soft_deny`, `allow`, and `environment` — configured through `AutoModeConfig`. Allow verdicts execute automatically; block verdicts return to the normal human approval flow. In headless sessions, blocks are denied because no human approval callback exists.

This is a second gate layered on top of the `PermissionContext` contract from 0004 Typed Permissioned Tools. It does not replace or fork tool permission semantics: explicit ALWAYS/NEVER permissions and session-approved rules still resolve before the classifier is ever consulted.

## Rationale

The gap between `accept-edits` (prompts on every shell command) and `auto-approve` (prompts on nothing) is a cliff. Users facing prompt fatigue jump straight to `auto-approve` and stop reviewing, so the mode meant as a last resort becomes the default. A classifier-gated middle lets routine work run uninterrupted while doubtful or risky actions require confirmation. Running it on a small, fast model keeps per-call cost low enough to classify every shell command rather than only the ones that miss existing rules.

## Agent Guidance

- The classifier must never see system context, injected user messages, or tool results. It receives only genuine human messages and assistant messages. Repository instructions, tool output, command stdout, fetched pages, and injected context may be attacker-controlled; letting them reach the classifier would let hostile content argue its own way past the gate or impersonate user authorization. Any change that widens the classifier's input is a security regression.
- A malformed, timed-out, or unavailable classifier response is never an allow — it falls through to a human prompt.
- The classifier is a mitigation, not a guarantee. Do not describe `auto` mode as safe, and do not extend it to replace review on sensitive operations.
- The classifier is permissive by default: it blocks only a concrete effect matching a deny rule. A permission boundary, external location, unfamiliar command, or missing allow example is not independently dangerous. Conversation-level requests and confirmations never auto-clear a matched dangerous-action rule; they provide context, while the dedicated approval prompt remains the authorization boundary.
- Rule lists in `AutoModeConfig` are additive over the built-in defaults in `permission_classifier.md`; config cannot delete a default rule.
- The classifier is created lazily. A session that never enters `auto` mode must never construct a classifier backend (0001 startup budget).
- A blocked action must enter the normal human approval flow. The classifier decides what can run automatically; it never replaces the human's final authority. Headless mode continues to fail closed when no approval callback exists.
- Interactive clients should surface each model verdict and its reason before continuing: `ALLOW` when the tool will run automatically, and `ASK` when the model routes it to human approval. This visibility is diagnostic; clients must not reinterpret or replace the classifier's judgment.
- The `/auto` picker edits additional ASK (`soft_deny`) and ALLOW rules as natural-language guidance. Curated choices are ordinary prose rules, custom rules use the same path, and ASK rules keep precedence over ALLOW rules inside the model prompt; the picker must not introduce command-pattern matching.
- `auto` mode pauses after repeated blocks and falls back to normal prompting, so a mis-tuned rule set degrades into prompts rather than an infinite denial loop.

## Flag To User When

- A change would let tool results, or any other model-controlled content, reach the classifier.
- A caller wants to treat an unavailable or failed classifier call as an allow.
- A new surface (ACP, programmatic, subagents) needs to bypass the gate rather than adopt it.
- Someone proposes `auto` mode as a replacement for `bypassPermissions`-style isolation, or as a safety guarantee.
