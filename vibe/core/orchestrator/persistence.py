from __future__ import annotations

import datetime
from pathlib import Path
from typing import TypedDict
import yaml

class StateDict(TypedDict):
    progress: int
    phase: str
    current_task: str | None

class PersistenceManager:
    def __init__(self, workdir: Path = Path(".")) -> None:
        self.workdir = workdir
        self.state_file = workdir / "STATE.md"
        self.log_file = workdir / "DECISION_LOG.md"

    def load_state(self) -> StateDict:
        if not self.state_file.exists():
            return {"progress": 0, "phase": "init", "current_task": None}

        content = self.state_file.read_text(encoding="utf-8")
        # Try to parse frontmatter or look for specific lines
        # Simple parsing for now: look for a yaml block or specific markers
        # Let's assume the state is stored as a YAML block in the MD for now
        try:
            if "```yaml" in content:
                yaml_block = content.split("```yaml")[1].split("```")[0]
                data = yaml.safe_load(yaml_block)
                return {
                    "progress": data.get("progress", 0),
                    "phase": data.get("phase", "init"),
                    "current_task": data.get("current_task"),
                }
        except Exception:
            pass

        return {"progress": 0, "phase": "init", "current_task": None}

    def save_state(self, state: StateDict) -> None:
        content = f"""# Project State

```yaml
progress: {state['progress']}
phase: "{state['phase']}"
current_task: "{state['current_task'] or ''}"
updated_at: "{datetime.datetime.now().isoformat()}"
```

## Description
Current phase: {state['phase']}
Progress: {state['progress']}%
"""
        self.state_file.write_text(content, encoding="utf-8")

    def log_decision(self, context: str, verdict: str) -> None:
        timestamp = datetime.datetime.now().isoformat()
        entry = f"""
## [{timestamp}] Decision

**Context:**
{context}

**Verdict:**
{verdict}

---
"""
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(entry)
