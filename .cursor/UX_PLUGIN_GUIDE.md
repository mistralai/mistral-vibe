# Plugins Mistral Vibe — Guide unifié

## Vue d'ensemble

**5 plugins** par type de skill, pour une utilisation et une compréhension simplifiées :

| Plugin | Description |
|--------|-------------|
| `/plugin ui-ux` | UI/UX — audits, accessibilité, design system, composants, briefs |
| `/plugin context-awareness` | Nouvelles sources de contexte — écran, clipboard, git diff, terminal pipe, navigateur |
| `/plugin memory-persistence` | Mémoire persistante, VIBE.md projet, RAG local |
| `/plugin collaboration-multi-agent` | Branches, sessions partagées, sous-agents spécialisés |
| `/plugin automatisation-triggers` | Tâches planifiées, webhooks, watch fichiers, macros |

Taper `/plugin` seul affiche la liste des plugins disponibles.

## Plugin UI/UX

Agent **Design** dédié. Lance avec `vibe --agent design` ou `./run-vibe-ux.sh`.

- 6 **outils** : analyze_design, accessibility_audit, component_recommender, design_system_check, color_palette_analyzer, typography_auditor
- 1 **skill** : ui-ux (regroupe audit, design system, composants, accessibilité, brief)
- **Auto-approve** sur les outils read-only design

### Usage

```bash
uv run vibe --agent design "/plugin ui-ux"
uv run vibe --agent design "/plugin ui-ux screenshot.png"
uv run vibe --agent design "/plugin ui-ux Améliore l'accessibilité de index.html"
```

## Plugin Context awareness

Enrichit le contexte avec : screen capture, clipboard watcher, git diff live, terminal pipe, browser companion.

```bash
vibe "/plugin context-awareness Review mon git diff"
commande | vibe "Analyse ce résultat"
```

## Plugin Memory & Persistence

Mémoire persistante (~/.vibe/memory.md), VIBE.md projet, embeddings local (RAG).

```bash
vibe "/plugin memory-persistence Retiens que je préfère TypeScript"
vibe "/plugin memory-persistence Génère le VIBE.md pour ce projet"
```

## Plugin Collaboration & Multi-agent

Branches de conversation, sessions partagées, délégation à des sous-agents (code, test, review).

```bash
vibe "/plugin collaboration-multi-agent Fork ici pour l'approche B"
vibe "/plugin collaboration-multi-agent Partage cette session"
```

## Plugin Automatisation & Triggers

Tâches planifiées, webhooks CI/GitHub, watch mode, macro skills.

```bash
vibe "/plugin automatisation-triggers"
vibe --at 09:00 "vérifie les PRs ouvertes"
vibe --watch src/ "analyse chaque changement"
```

## Configuration

Clé Mistral : `MISTRAL_API_KEY` (env ou `~/.vibe/.env`).

```toml
[tools.analyze_design]
model = "mistral-small-latest"

[tools.accessibility_audit]
wcag_level = "AA"
```

## Lancement depuis le projet

Une installation globale (`uv tool install mistral-vibe`) utilise la version PyPI sans ces plugins. Toujours lancer depuis le projet avec `uv run vibe` ou `./run-vibe-ux.sh`.
