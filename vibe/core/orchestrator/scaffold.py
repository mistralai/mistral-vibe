from __future__ import annotations

from pathlib import Path
from typing import Any

# We import inside the function or here to avoid issues if dependency is missing in some envs,
# though we added it to pyproject.toml.
try:
    from cookiecutter.main import cookiecutter
except ImportError:
    cookiecutter = None

class Scaffolder:
    def __init__(self, workdir: Path = Path(".")) -> None:
        self.workdir = workdir

    def run_scaffold(self, template_url: str, no_input: bool = True, extra_context: dict[str, Any] | None = None, **kwargs: Any) -> Path:
        """
        Runs cookiecutter with the given template.
        Returns the output directory.
        """
        if cookiecutter is None:
            raise ImportError("cookiecutter is not installed")

        output_dir = cookiecutter(
            template_url,
            output_dir=str(self.workdir),
            no_input=no_input,
            extra_context=extra_context,
            **kwargs
        )
        return Path(output_dir)

    def list_recommended_templates(self) -> list[dict[str, str]]:
        return [
            {"name": "Python Package", "url": "https://github.com/audreyfeldroy/cookiecutter-pypackage"},
            {"name": "FastAPI", "url": "https://github.com/tiangolo/full-stack-fastapi-template"},
            {"name": "Mistral Vibe Plugin", "url": "https://github.com/mistralai/cookiecutter-vibe-plugin"},
        ]
