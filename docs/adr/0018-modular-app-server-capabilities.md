# 0018 Modular App-Server Capabilities

## Decision

New agent behaviors, custom tools, built-in plugins and skills belong to
app-server feature modules using existing Runtime capabilities.

Each top-level RPC family has one explicitly registered module owner; each
method has one handler owner. The dispatcher handles transport concerns.
Modules own feature behavior, route validation and product policy.

Expose narrow contracts with typed values, errors and events. Inject only used
capabilities; do not pass the whole server, use service locators, inspect another
feature's private state or create import cycles. Collaboration declares ordering,
cancellation and cleanup ownership. Shared helpers contain no feature policy.

Apply these rules to new features and substantive changes. Improve the touched
boundary without requiring an unrelated whole-server refactor.

## Rationale

Isolated contracts keep changes local and allow module implementations to evolve
without spreading framework or private-state dependencies.

## Agent Guidance

- Identify the owning module and required capabilities before implementation.
- Register RPC ownership and verify behavior through the module/API contract.
- If existing capabilities cannot express the feature entirely in app-server
  code, explain the missing Runtime/Core primitive, why alternatives fail and
  the proposed scope before editing Runtime/Core. Proceed once agreed; an
  explicit user request for that Runtime/Core change supplies agreement.

## Flag To User When

- Required Runtime/Core scope has not already been agreed with the user.
- A shortcut introduces private cross-feature access or cyclic dependencies.
