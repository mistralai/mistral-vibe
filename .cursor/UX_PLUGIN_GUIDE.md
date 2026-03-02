# Mistral Vibe Plugins — Guide

## Overview

The app exposes a single plugin:

| Plugin | Description |
|--------|-------------|
| `/plugin ux-ui` | UX/UI best practices: audits, accessibility, design systems, components, briefs |

`/plugin` shows this single plugin.

## Plugin UX/UI

Dedicated **Design** agent. Run with `vibemic --agent design`.

- Design tools: `analyze_design`, `accessibility_audit`, `component_recommender`, `design_system_check`, `color_palette_analyzer`, `typography_auditor`
- Single skill: `ux-ui`
- Goal: apply actionable UX/UI recommendations on subsequent requests

## Session Activation

- `vibemic "/plugin ux-ui"` enables UX/UI mode.
- Chat confirms activation without showing internal `SKILL.md` content.
- A visual indicator appears in the bottom-right corner while mode is active.
- Following messages are automatically enriched with UX/UI context.
- `vibemic "/plugin off"` disables plugin mode.

## Expected Output

- Fast diagnosis with UX/UI score
- Prioritized issue list (critical → low)
- Concrete, testable action plan
- Code changes only on request

## Covered Best Practices

- Visual hierarchy and readability
- Contrast, keyboard navigation, labels, semantics
- Component/token/design-system consistency
- Mobile-first responsiveness
- Clear user flows

### Usage

```bash
uv run vibemic --agent design "/plugin ux-ui"
uv run vibemic --agent design "/plugin ux-ui screenshot.png"
uv run vibemic --agent design "/plugin ux-ui Improve accessibility in index.html"
uv run vibemic --agent design "/plugin off"
```
