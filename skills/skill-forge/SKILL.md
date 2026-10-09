---
name: skill-forge
description: |
  Interactive assistant for creating, testing, and installing mistral-vibe skills, agents, loops, and workflows.
  Debug user-created skills with step-by-step interactive guidance. Enforces hard gating via custom middleware.
  Saves and restores existing agent loops and middleware with labeled persistence.
  Uses runtime activation: intercepted before normal skill execution for deterministic mode switch.
user-invocable: true
allowed-tools:
  - AskUserQuestion
  - Bash
  - Read
  - Write
  - Edit
  - Skill
  - Agent
  - EnterPlanMode
  - ExitPlanMode
  - TaskCreate
  - TaskUpdate
  - TaskList
  - TaskGet
  - TaskStop
  - WebFetch
  - WebSearch
  - PushNotification
  - CronCreate
  - CronDelete
  - CronList
  - ScheduleWakeup
  - NotebookEdit
  - RemoteTrigger
  - EnterWorktree
  - ExitWorktree
  - Monitor
activation:
  type: runtime
  entrypoint: skills.skill-forge.agent_loop:enter_skill_forge
  exitpoint: skills.skill-forge.agent_loop:exit_skill_forge
---

# Skill Forge

Interactive skill/agent/loop/workflow creation and debugging assistant with hard gating and state persistence.

## Execution Graph (Runtime Control Flow)

```
User: /skill-forge
  ↓
RuntimeActivation (SkillManager intercepts)
  ↓
enter_skill_forge()

Runtime Transition:
  ├─ Snapshot current runtime (middleware, agent, config)
  ├─ Clear middleware pipeline (save live objects for same-process restore)
  ├─ Inject SkillForgeMiddleware
  ├─ Switch to skill-forge agent profile
  └─ Enter controlled loop

Controlled Loop:
  SkillForgeMiddleware (hard gating, enforces user confirmation)
     ↓
  skill-forge agent (guided workflow)
     ↓
  staged artifact generation (skills/, agents/, prompts/)

Exit:
  exit_skill_forge()
     ↓
  Commit OR Discard staged artifacts
     ↓
  Restore middleware stack (live objects)
     ↓
  Restore agent profile
     └─ Cleanup runtime state
```

## When to Use
- Creating new mistral-vibe skills, agents, loops, or workflows.
- Debugging user-created skills that aren't working as intended.
- Needing step-by-step interactive guidance for skill development.

## Interactive Experience (After Activation)

Once activated, the runtime calls `enter_skill_forge()` which:

1. **Saves** current state (middleware, agent, config) to `~/.vibe/skill-forge-state/current_session.json`
2. **Clears** middleware pipeline (saves live objects for same-process restore)
3. **Loads** `SkillForgeMiddleware` into pipeline
4. **Switches** to `skill-forge-agent` loop
5. **Stages** all new artifacts to `~/.vibe/skill-forge-state/staging/<session_id>/`

### Interactive Workflow

Uses `AskUserQuestion` for EACH PHASE. Middleware enforces waiting for user input.

#### Phase1: Goal Elicitation
Ask user to choose:
- Create new component (skill/agent/loop/workflow)
- Debug existing component

#### Phase2: Requirements Gathering
Elicit: names, descriptions, tools, configs. Validate. Wait for confirmation.

#### Phase3: Construction
Create files in **staging**, not live paths. Wait for confirmation.

#### Phase4: Functional Verification
Beyond syntax:
1. Discovery test
2. Invocation test
3. Agent test
4. Integration test

Wait for confirmation.

#### Phase5: Installation/Validation
Run validation on staged artifacts. Wait for confirmation.

---

## VERY LAST ACTION

After ALL phases complete:

### Prompt User
```python
AskUserQuestion(questions=[{
    "question": "Do you want to apply/install the components you just created?",
    "options": [
        {"label": "Apply", "description": "Commit staged artifacts to live paths"},
        {"label": "Save only", "description": "Keep in saved/ for later reuse"},
        {"label": "Discard", "description": "Delete staging, restore previous state"},
        {"label": "Continue", "description": "Stay in Skill Forge mode"},
    ]
}])
```

### If Apply:
```python
exit_skill_forge(apply_user_work=True)  # Commits staging → live, then restores
```

### If Save Only:
```python
from skills.skill-forge.agent_loop import save_for_reuse
save_for_reuse(label="my-work")
# Stay in forge mode or exit
```

### If Discard:
```python
# Save created files for later use/refinement before exiting
from skills.skill-forge.agent_loop import save_for_reuse
save_for_reuse(label="discarded-" + str(session_id))
exit_skill_forge(apply_user_work=False)  # Restores previous state, keeps saved files
```

### If Continue:
Return to Phase1.

---

## Hard Gating Rules
- Middleware blocks turns until user confirms via `AskUserQuestion`
- No skipping phases without user confirmation
- No information dumping: provide only current phase info
- Ask clarifying questions if user input is ambiguous

## State Persistence
- **Staging**: `~/.vibe/skill-forge-state/staging/<session_id>/`
- **Saved**: `~/.vibe/skill-forge-state/saved/<label>/` (includes discarded work)
- **Current state**: `~/.vibe/skill-forge-state/current_session.json`
- **Live objects** saved for same-process restore (fast path)
- **Disk descriptors** for crash/restart recovery
- Restored exactly on cleanup, preserving integrity
- Discarded work is saved (not deleted) for later use/refinement
