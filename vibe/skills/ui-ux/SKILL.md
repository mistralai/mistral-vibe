---
name: ui-ux
description: UI/UX — audits, accessibility, design system, components, briefs
license: Apache-2.0
user-invocable: true
allowed-tools:
  - read_file
  - grep
  - analyze_design
  - accessibility_audit
  - component_recommender
  - design_system_check
  - color_palette_analyzer
  - typography_auditor
  - search_replace
  - write_file
  - ask_user_question
---

# Plugin UI/UX

Unified plugin for all design-related tasks: audits, accessibility, design systems, components, briefs.

## Usage modes

Based on the user's request, choose the appropriate mode:

### Full design audit
Image file (screenshot, mockup) or path to design files:
- Image → `analyze_design` (hierarchy, spacing, colors, UX)
- HTML/CSS → `accessibility_audit`, `color_palette_analyzer`, `typography_auditor`
- Score 0–10, prioritized issues, concrete recommendations

### Design system
Check conformity to Material, Tailwind, tokens. Use `design_system_check`.

### Components
Component review and consolidation. Use `component_recommender`.

### UX / responsive / mobile
Mobile-first review, responsive layout, user journey.

### Accessibility
WCAG improvement plan. Use `accessibility_audit`, suggest fixes.

### Design brief
Design specification, requirements, action plan.

## Usage

- `vibe "/plugin ui-ux"` — general design mode
- `vibe "/plugin ui-ux screenshot.png"` — audit a screenshot
- `vibe "/plugin ui-ux Improve accessibility of index.html"`
- `vibe "/plugin ui-ux Design system conformity in src/"`
