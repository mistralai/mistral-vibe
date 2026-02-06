from __future__ import annotations

from pathlib import Path

class Architect:
    def __init__(self, workdir: Path = Path(".")) -> None:
        self.workdir = workdir
        self.spec_file = workdir / "SPEC.md"
        self.arch_file = workdir / "ARCHITECTURE.md"

    def validate_spec(self) -> bool:
        """Checks if SPEC.md exists and is not empty."""
        return self.spec_file.exists() and len(self.spec_file.read_text().strip()) > 0

    def ensure_architecture_doc(self) -> None:
        if not self.arch_file.exists():
            content = """# Architecture

```mermaid
graph TD
    User -->|Input| CLI
    CLI --> Core
```
"""
            self.arch_file.write_text(content, encoding="utf-8")

    def generate_diagram_prompt(self, file_structure: str) -> str:
        """Returns a prompt to generate a Mermaid diagram based on file structure."""
        return f"""
Based on the following file structure, generate a Mermaid class diagram or flow chart that represents the architecture.
Output ONLY the mermaid block.

Structure:
{file_structure}
"""
