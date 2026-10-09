---
name: vibe-rust-tui
description: Ratatui UI conventions for the Mistral Vibe Rust CLI (vibe/cli-rust). Use when adding or changing any screen, list, picker, footer hint, keyboard shortcut, list navigation or scrolling, back/close behavior, search field, selection highlight, color, or onboarding/dialog copy in the Rust TUI.
metadata:
  display-name: Vibe Rust TUI
  short-description: Hint, keyboard, navigation, search, list-cursor and color conventions for the Rust TUI
  default-prompt: Use $vibe-rust-tui to follow the Rust TUI hint, keyboard, navigation, search, list-cursor and color conventions.
---

# Vibe Rust TUI

UI conventions for `vibe/cli-rust/`. Every screen follows them so the TUI reads as one product. Process rules (porting from Python, scenarios, goldens) live in `migrate-cli-to-rust`; local code rules live in `vibe/cli-rust/AGENTS.md`.

## Shortcut hints

- Name keys and actions only through `src/hints.rs`: `key::*`, `action::*` and the shared pairs (`hints::NAVIGATE`, `SELECT`, `SEARCH`, `CANCEL`, `CLOSE`, `BACK`). Never write a key or action as a string literal at a call site. A single letter key (`"d"`, `"r"`) is fine as long as its action comes from `action::*`. When a key or verb is missing, add it to `src/hints.rs`.
- A hint is an unpadded `(key, action)` pair (`hints::Hint`). The renderer owns all spacing; never bake spaces into a label.
- Wording: actions are lowercase verbs (`navigate`, `select`, `close`); keys keep their canonical casing (`Enter`, `Esc`, `Ctrl+C`, `Backspace`). Combined keys use `/` (`Space/Enter`, `Esc/Ctrl+C`).
- Vertical navigation is `key::NAV` (`↑↓/jk`) whenever j/k work; use `key::ARROWS` (`↑↓`) only where a focused text field owns the letters. Horizontal navigation is `key::LEFT_RIGHT` (`←→`).
- Footers read `key action  key action`, ending with the Esc hint. Inline status lines (loading line, narrator) are the one exception and read `key to action`.
- Render every footer through `ui/hint_line.rs` (`draw`, `draw_clipped`, `line`, `spans`, `styled`): they apply the shared key/action styles and clip to the box. Never place hint text with hardcoded x offsets or custom key styles. Center a hint with `hints::width`.
- Onboarding and dialogs follow the same footer form (`Enter continue`, `Esc back`), not sentences like "Press Esc to go back".

## Keyboard navigation

- Every navigable list accepts `j`/`k` alongside `↑`/`↓` when no text field is focused. Match only plain keys (no Ctrl/Alt/Super) so `Ctrl+J`/`Ctrl+K` keep their editing meaning.
- Show the navigation keys a screen actually handles: write `↑↓/jk` only where j/k are wired.
- Edges: `↑↓`/`jk` (and `←→` across dialog options) wrap at both ends; PageUp/PageDown/Home/End clamp. Move through `list_nav::wrap`, or `list_nav::wrap_selectable` when some rows (section headers, notes, blanks) cannot take the cursor; never hand-roll `saturating_sub`/`min(len - 1)` for arrow moves. The only lists that do not wrap are the ones attached to the composer: Down past the newest queued prompt leaves queue mode, and Up at the top of the subagent list returns focus to the composer.
- Back: Esc goes back exactly one level (field → list, detail → list, auth flow → its `/mcp` list) and closes only at the top level. Backspace edits text and is never a navigation key. A nested view's footer ends with `hints::BACK` (`Esc back`); a top-level view's footer ends with `hints::CLOSE` or `hints::CANCEL`.
- Digits: a row's digit shortcut exists if and only if the row displays its number (`1. Trust folder`). Pressing it is the same as moving to the row and pressing Enter. Parse it with `list_nav::digit`; never accept digits on unnumbered rows, and never number rows that do not accept their digit.

## Scrolling

- A scrolling list keeps the highlighted option in view through `ui/list_scroll.rs` `follow`, passing which lines are options; never write its own `scroll = selected` reconciliation. It moves the viewport the least and brings the option's non-selectable neighbours along: its section header above it, the list's leading lines for the first option, the trailing lines for the last one. Wrapping from the bottom back to the top therefore always shows the very top of the list.
- Mouse-wheel scrolling sets the list's `free_scroll` and skips `follow` until the next keyboard move; it only clamps the offset.

## Searchable lists

- Use `search_field::Search`, `search_field::handle_key` and `search_field::paste` for any filterable list, and build its footer with `search_field::hints`.
- The model: lists open with the list focused; `/` focuses the field; typing filters live (letters, including j/k, go to the field); `↑↓`, PageUp/PageDown and Enter keep acting on the list while the field is focused; Esc first leaves the field (the filter stays), then clears the filter, then closes the screen.
- Do not add other entry points into search (Tab, Left, type-to-search on open). Do not make Enter only "apply" the filter: it acts on the highlighted item.
- The list footer includes `hints::SEARCH`.
- The search row sits directly under the title, followed by one blank row, and reads `/  placeholder` (`Search plugins`, `Search settings`). It is always visible in the list view and hidden in detail views. Draw it only with `ui/search_field.rs` `draw_row`, which owns the `/` and the field's indent; never put a search field at the bottom of a list or in the footer. The project picker is the one exception: its labelled `Search projects:` field shares the form `Field` of its create view.

## Selection

- The focused row is the list-cursor bar: `ui/list_cursor.rs` `style()` across the full row width (block-cursor colors, bold). No glyph cursors (`›`, `▸`, `>`) for focus.
- Draw the bar only through `list_cursor`: `paint(f, area)` fills a row area, `styles(highlighted)` / `styles_on(highlighted, bg)` give the `(text, dim)` styles of a row, `marker_style` styles the current-value marker on it. Never read `theme::block_cursor_bg`/`block_cursor_fg` outside `ui/theme/` and `ui/list_cursor.rs` (a unit test enforces it). Text-field carets are not list cursors: use `theme::block_caret` (TextArea-like fields) or `theme::fixed::input_caret` (compact `Input`-like fields).
- Horizontal options (dialog buttons) render each option as a ` label ` chip; the selected chip takes `list_cursor::style()`. Keep labels at the same columns when switching selection.
- The current value (the active model, theme, level, layer or session) is marked with `list_cursor::marker(true)` (`› `) in `list_cursor::current_color()`, in a two-column slot that stays blank (`list_cursor::BLANK`) on other rows.
- `(String, Style)` row renderers paint the full-width bar for rows where `list_cursor::is_bar` holds, and keep a span's own background instead of overwriting it.

## Colors

- Every color value lives in `src/ui/theme/`: theme-dependent colors in the theme palettes (`theme::foreground()`, `theme::muted_style()`, ...), fixed brand and terminal colors in `ui/theme/fixed.rs` (`ORANGE`, `LOADING_GRADIENT`, onboarding colors, `sgr::*` escape codes, the Monokai/Alabaster named palette via `fixed::named`), syntax colors in `ui/theme/syntax.rs`. A fixed color such as the brand orange is fine, but it is defined once there and imported everywhere else.
- Outside `src/ui/theme/`, never write `Color::Rgb(..)`, `Color::Indexed(..)`, a named `Color::*` variant or a raw SGR color code. When a screen needs a new color, add a named constant or theme accessor first. `tests/units/color_literals.rs` fails on any literal outside the theme module (only `update_prompt/ansi.rs`, which converts any `Color` to SGR, and test code are exempt).

## Verification

- Any change to a hint, a key, a list behavior (wrap, back, digits, scroll, search) or a selection style is user-visible: update the affected `client-e2e` scenarios (`screen_contains`, key sequences, mouse coordinates) and regenerate their goldens following `migrate-cli-to-rust`. A new behavior gets its own scenario (for example `mcp/mcp_wrap_scroll`).
- Shared helpers carry their own unit tests (`tests/units/list_nav.rs`, `list_scroll.rs`, `search_field.rs`); extend them when a helper changes instead of testing the rule screen by screen.
- `tests/units/color_literals.rs` guards the color and list-cursor rules. Do not add exemptions to make it pass: move the color into `src/ui/theme/` or route the bar through `list_cursor`.
