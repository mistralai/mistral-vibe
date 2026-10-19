# 0010 Textual Content Rendering

## Decision

Textual is used only by the `vibe-acp --setup` onboarding wizard
(`vibe/setup/onboarding`); this decision applies to that code. The terminal UI
is the Rust CLI ([0016](0016-rust-cli-delivery-surface.md)).

Render styled text with Textual `Content` and apply styles on it directly (`Content.assemble`, `Content.styled`). Use Rich `Text` or `escape()` only when strictly necessary. Be careful with parsing: keep dynamic text out of markup parsers.

## Rationale

`Content` is theme-aware — theme variables (`$primary`, `$error`) resolve against the active theme at render, staying consistent across themes. Rich `Text` uses fixed styles and crashes on a `$` variable (`MissingStyle`). Parsing markup from dynamic text is unsafe; `escape()` isn't fully trustworthy.

## Agent Guidance

- Reach for `Content` first; use Rich `Text` only when a widget contract requires it.
- Apply styles on `Content` directly; don't build markup strings.
- Never put a `$` theme variable on a Rich `Text` span.
- Pass dynamic or user text as plain segments, never through a parser.
- Reuse shared style constants (e.g. `SHORTCUT_STYLE`) instead of redefining them.

## Flag To User When

- A span needs a theme variable but sits in Rich `Text` (migrate to `Content`).
- Markup is assembled from dynamic input, or `escape()` is used to make it "safe".
- A widget accepts only Rich renderables and blocks moving to `Content`.
- A change adds Textual code outside the onboarding wizard.
