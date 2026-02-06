from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

class PlanningSystem:
    def __init__(self, workdir: Path = Path(".")) -> None:
        self.workdir = workdir
        self.brainfile = workdir / "brainfile.md"

    def ensure_brainfile(self) -> None:
        if not self.brainfile.exists():
            self.brainfile.write_text("# Brainfile\n\n- [ ] Initial Task\n", encoding="utf-8")

    def get_tasks(self) -> list[dict[str, str | bool]]:
        if not self.brainfile.exists():
            return []

        content = self.brainfile.read_text(encoding="utf-8")
        tasks = []
        for line in content.splitlines():
            match = re.match(r"^\s*-\s*\[([ xX])\]\s*(.*)$", line)
            if match:
                completed = match.group(1).lower() == "x"
                text = match.group(2).strip()
                tasks.append({"text": text, "completed": completed})
        return tasks

    def get_active_task(self) -> Optional[str]:
        for task in self.get_tasks():
            if not task["completed"]:
                return task["text"]  # type: ignore
        return None

    def update_from_reflection(self, text: str) -> None:
        # Extract ai_plan, ai_notes, ai_review blocks
        # And append them to the brainfile or specific task section
        # For simplicity, we append to the end of brainfile

        blocks = []
        for tag in ["ai_plan", "ai_notes", "ai_review"]:
            pattern = f"<{tag}>(.*?)</{tag}>"
            matches = re.findall(pattern, text, re.DOTALL)
            for content in matches:
                blocks.append(f"### {tag.upper().replace('_', ' ')}\n{content.strip()}\n")

        if blocks:
            with self.brainfile.open("a", encoding="utf-8") as f:
                f.write("\n" + "\n".join(blocks))

    def mark_task_complete(self, task_text: str) -> None:
        if not self.brainfile.exists():
            return

        content = self.brainfile.read_text(encoding="utf-8")
        lines = content.splitlines()
        new_lines = []
        for line in lines:
            match = re.match(r"^\s*-\s*\[([ xX])\]\s*(.*)$", line)
            if match:
                text = match.group(2).strip()
                if text == task_text:
                    new_lines.append(line.replace("[ ]", "[x]", 1))
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        self.brainfile.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def add_task(self, task_text: str) -> None:
        self.ensure_brainfile()
        with self.brainfile.open("a", encoding="utf-8") as f:
            f.write(f"- [ ] {task_text}\n")
