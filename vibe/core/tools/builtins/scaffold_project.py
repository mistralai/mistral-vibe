from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field

from vibe.core.orchestrator.scaffold import Scaffolder
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolStreamEvent,
)


class ScaffoldProjectArgs(BaseModel):
    template_url: str = Field(description="The URL of the cookiecutter template to use")
    extra_context: dict[str, Any] = Field(
        default_factory=dict, description="Extra context values for the template"
    )


class ScaffoldProjectResult(BaseModel):
    output_dir: str


class ScaffoldProject(
    BaseTool[ScaffoldProjectArgs, ScaffoldProjectResult, BaseToolConfig, BaseToolState]
):
    description = "Scaffold a new project using a cookiecutter template. Use this when the user asks to create a new project structure from a template."

    async def run(
        self, args: ScaffoldProjectArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ScaffoldProjectResult, None]:
        scaffolder = Scaffolder(Path.cwd())
        try:
            # We enforce no_input=True to avoid blocking the agent
            output_dir = scaffolder.run_scaffold(
                args.template_url, no_input=True, extra_context=args.extra_context
            )
            yield ScaffoldProjectResult(output_dir=str(output_dir))
        except ImportError:
            raise ToolError("Cookiecutter is not installed.")
        except Exception as e:
            raise ToolError(f"Scaffolding failed: {e}")
