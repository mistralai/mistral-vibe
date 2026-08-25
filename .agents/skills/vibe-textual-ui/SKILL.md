---
name: vibe-textual-ui
description: Textual TUI widget and TCSS conventions for Mistral Vibe. Use when building or styling Textual widgets, writing TCSS rules, working with selectable lists, tool headers, theme variables, or UI component layout.
metadata:
  display-name: Vibe Textual UI
  short-description: Widget and TCSS conventions for Vibe
  default-prompt: Use $vibe-textual-ui to follow Vibe Textual UI conventions when building or styling widgets.
---

# Vibe Textual UI

Conventions for building and styling Textual TUI widgets in Vibe. Apply when working in `vibe/cli/`, creating or modifying widgets, or writing TCSS.

## Widgets

- For selectable lists, use `NavigableOptionList` from `vibe/cli/textual_ui/widgets/navigable_option_list.py` instead of Textual's `OptionList`. It adds `j`/`k` cursor navigation on top of the arrow keys; the bare `OptionList` only handles arrows.
- Keep feature-specific Textual state and helper functions with the feature's widget package. `app.py` should orchestrate mounting and message handling, not accumulate feature-local state models, defaulting helpers, or import factories.
- Render tool headers as an explicit verb plus message in every state: progressive wording while a call is running, then a settled verb once any result arrives, including a failure. Preserve result metadata such as `(truncated)` or `(scratchpad)` immediately after the message and keep error details in the expandable result body.

## TCSS

- When a rule sets `color: $text-muted;`, pair it with a nested `&:ansi { text-style: dim; }` so the muted intent survives under ANSI themes.
- Never use `ansi_*` colors (e.g. `ansi_red`, `ansi_bright_blue`). Use Textual theme variables like `$primary`, `$foreground`, `$surface`, `$error`, etc. — see https://textual.textualize.io/guide/design/. ANSI themes are derived from these variables automatically.
- Keep truncation and overflow behavior declarative in TCSS. Do not add resize handlers or explicit widget-width calculations solely to force an ellipsis; accept clipping or overflow when Textual cannot express the layout reliably.
