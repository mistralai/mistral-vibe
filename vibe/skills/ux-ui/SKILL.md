---
name: ux-ui
description: UX/UI best practices for audits, accessibility, design systems, and components
license: Apache-2.0
user-invokable: true
---

# Plugin UX/UI

Single UX/UI plugin with **persistent mode**.
After activation with `/plugin ux-ui`, subsequent user requests are interpreted with this UX/UI context to improve output quality.

## Goal

Provide clear, prioritized, directly implementable recommendations with focus on:
- interface readability,
- WCAG accessibility,
- design system consistency,
- component quality,
- desktop/mobile robustness.

## Method

1. Clarify product goal and target user journey.
2. Assess current state (screenshot, HTML/CSS, components).
3. Prioritize issues (critical, high, medium, low).
4. Propose concrete, verifiable fixes.
5. If requested, apply changes file by file.

## Expected Session Behavior

- On activation (`/plugin ux-ui`), the app confirms that the plugin is enabled.
- Internal skill content is not shown in chat.
- Following requests are automatically enriched with this UX/UI pre-context.
- Mode stays active for the current session.
- Use `/plugin off` to disable plugin mode.

## Usage Modes

Choose the most suitable mode based on the user request:

### Full Design Audit
Image (screenshot, mockup) or design files:
- Image → `analyze_design` (hierarchy, spacing, colors, UX)
- HTML/CSS → `accessibility_audit`, `color_palette_analyzer`, `typography_auditor`
- Score 0–10, prioritized issues, actionable recommendations

### Design system
Check consistency with Material, Tailwind, and tokens. Use `design_system_check`.

### Components
Review and consolidate components. Use `component_recommender`.

### UX / responsive / mobile
Review mobile-first behavior, responsive layout, and user flows.

### Accessibility
Build a WCAG improvement plan. Use `accessibility_audit`, propose concrete fixes.

### Design Brief
Create design specification, requirements, and action plan.

## Expected Output Format

- **Score**: 0–10
- **Top issues**: prioritized list (critical first)
- **Recommendations**: concrete actions, expected impact, estimated effort
- **Code patch**: only when user asks for implementation
- **UX rationale**: why each change improves user experience

## Rules

- Stay concrete and concise.
- Justify each recommendation with user impact.
- Preserve existing design system when consistent.
- Avoid introducing unnecessary complexity.

## Usage

- `vibemic "/plugin ux-ui"` — general design mode
- `vibemic "/plugin ux-ui screenshot.png"` — audit a screenshot
- `vibemic "/plugin ux-ui Improve accessibility in index.html"`
- `vibemic "/plugin ux-ui Check design system consistency in src/"`
- `vibemic "/plugin off"` — disable plugin mode

## Example Requests After Activation

- `Make this form clearer and more accessible.`
- `Propose a mobile-first version of this page.`
- `Prioritize the top 5 blocking UX issues.`
