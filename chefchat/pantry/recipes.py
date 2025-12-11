"""ChefChat Recipe Engine - YAML Workflow Parser & Executor.

Recipes are standardized workflows defined in YAML files.
The Chef can "cook" a recipe to execute a series of steps,
delegating each step to the appropriate kitchen station.

"Follow the recipe" - consistency and reproducibility.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
import yaml


class StepType(str, Enum):
    """Types of steps in a recipe."""

    ANALYZE = "analyze"  # Analyze code/context
    GENERATE = "generate"  # Generate new code
    REFACTOR = "refactor"  # Refactor existing code
    TEST = "test"  # Run tests
    REVIEW = "review"  # Code review
    VERIFY = "verify"  # Verify packages/dependencies
    SHELL = "shell"  # Run shell command
    PROMPT = "prompt"  # Ask user for input


class RecipeStep(BaseModel):
    """A single step in a recipe."""

    name: str = Field(description="Human-readable step name")
    type: StepType = Field(description="Type of action to perform")
    station: str = Field(
        default="line_cook", description="Kitchen station to handle this step"
    )
    prompt: str | None = Field(
        default=None, description="Prompt or instruction for the step"
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="Input parameters for the step"
    )
    outputs: list[str] = Field(
        default_factory=list, description="Expected outputs from this step"
    )
    on_error: str = Field(
        default="abort",
        description="What to do on error: 'abort', 'continue', or 'retry'",
    )


class Recipe(BaseModel):
    """A complete recipe definition."""

    name: str = Field(description="Recipe name")
    description: str = Field(default="", description="What this recipe does")
    author: str = Field(default="ChefChat", description="Recipe author")
    version: str = Field(default="1.0.0", description="Recipe version")

    # Recipe metadata
    ingredients: list[str] = Field(
        default_factory=list, description="Required context/files for this recipe"
    )

    # The cooking steps
    steps: list[RecipeStep] = Field(
        default_factory=list, description="Ordered list of steps to execute"
    )

    # Variables that can be referenced in steps
    variables: dict[str, Any] = Field(
        default_factory=dict, description="Variables that can be used in prompts"
    )

    class Config:
        """Pydantic config."""

        extra = "allow"  # Allow extra fields for extensibility


class RecipeParser:
    """Parses and manages recipe YAML files.

    Recipes are stored in `.chef/recipes/` by default.
    """

    DEFAULT_RECIPE_DIR = ".chef/recipes"

    def __init__(self, project_root: str | Path) -> None:
        """Initialize the recipe parser.

        Args:
            project_root: Root directory of the project
        """
        self.project_root = Path(project_root)
        self.recipe_dir = self.project_root / self.DEFAULT_RECIPE_DIR
        self._recipes: dict[str, Recipe] = {}

    def ensure_recipe_dir(self) -> None:
        """Create the recipe directory if it doesn't exist."""
        self.recipe_dir.mkdir(parents=True, exist_ok=True)

    def list_recipes(self) -> list[str]:
        """List all available recipes.

        Returns:
            List of recipe names (without .yaml extension)
        """
        if not self.recipe_dir.exists():
            return []

        return [f.stem for f in self.recipe_dir.glob("*.yaml")] + [
            f.stem for f in self.recipe_dir.glob("*.yml")
        ]

    def load(self, recipe_name: str) -> Recipe:
        """Load a recipe by name.

        Args:
            recipe_name: Name of the recipe (without .yaml extension)

        Returns:
            The parsed Recipe

        Raises:
            FileNotFoundError: If recipe doesn't exist
            ValueError: If recipe is invalid
        """
        # Check cache
        if recipe_name in self._recipes:
            return self._recipes[recipe_name]

        # Find the recipe file
        yaml_path = self.recipe_dir / f"{recipe_name}.yaml"
        if not yaml_path.exists():
            yaml_path = self.recipe_dir / f"{recipe_name}.yml"

        if not yaml_path.exists():
            raise FileNotFoundError(f"Recipe not found: {recipe_name}")

        # Parse YAML
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

        if not raw:
            raise ValueError(f"Empty recipe: {recipe_name}")

        # Validate and create Recipe
        try:
            recipe = Recipe.model_validate(raw)
        except Exception as e:
            raise ValueError(f"Invalid recipe {recipe_name}: {e}") from e

        # Cache and return
        self._recipes[recipe_name] = recipe
        return recipe

    def save(self, recipe: Recipe, overwrite: bool = False) -> Path:
        """Save a recipe to disk.

        Args:
            recipe: The recipe to save
            overwrite: Whether to overwrite existing

        Returns:
            Path to the saved file
        """
        self.ensure_recipe_dir()

        path = self.recipe_dir / f"{recipe.name.lower().replace(' ', '_')}.yaml"

        if path.exists() and not overwrite:
            raise FileExistsError(f"Recipe already exists: {path}")

        # Convert to dict and dump
        data = recipe.model_dump(exclude_none=True)
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

        return path

    def get_recipe_info(self, recipe_name: str) -> dict[str, Any]:
        """Get metadata about a recipe without fully loading it.

        Args:
            recipe_name: Name of the recipe

        Returns:
            Dict with recipe metadata
        """
        recipe = self.load(recipe_name)
        return {
            "name": recipe.name,
            "description": recipe.description,
            "author": recipe.author,
            "version": recipe.version,
            "step_count": len(recipe.steps),
            "ingredients": recipe.ingredients,
        }


class RecipeExecutor:
    """Executes recipes step by step.

    Coordinates with the kitchen bus to delegate steps
    to the appropriate stations.
    """

    def __init__(self, parser: RecipeParser) -> None:
        """Initialize the executor.

        Args:
            parser: RecipeParser instance
        """
        self.parser = parser
        self._current_recipe: Recipe | None = None
        self._current_step: int = 0
        self._context: dict[str, Any] = {}

    async def execute(
        self,
        recipe_name: str,
        on_step: Any = None,
        on_complete: Any = None,
        on_error: Any = None,
    ) -> dict[str, Any]:
        """Execute a recipe.

        Args:
            recipe_name: Name of the recipe to execute
            on_step: Callback for each step (async)
            on_complete: Callback when complete (async)
            on_error: Callback on error (async)

        Returns:
            Execution results
        """
        import asyncio

        recipe = self.parser.load(recipe_name)
        self._current_recipe = recipe
        self._current_step = 0
        self._context = dict(recipe.variables)

        results: list[dict[str, Any]] = []

        for i, step in enumerate(recipe.steps):
            self._current_step = i

            try:
                if on_step:
                    await on_step(i, step, len(recipe.steps))

                # Create step payload
                payload = {
                    "step_index": i,
                    "step_name": step.name,
                    "step_type": step.type.value,
                    "prompt": self._interpolate(step.prompt or ""),
                    "inputs": self._interpolate_dict(step.inputs),
                    "recipe_name": recipe.name,
                }

                # For now, simulate step execution
                # In real implementation, this would send to bus
                await asyncio.sleep(0.5)  # Simulate work

                result = {
                    "step": i,
                    "name": step.name,
                    "status": "success",
                    "outputs": {},
                }
                results.append(result)

                # Store outputs in context
                for output in step.outputs:
                    self._context[output] = result.get("outputs", {}).get(output)

            except Exception as e:
                error_result = {
                    "step": i,
                    "name": step.name,
                    "status": "error",
                    "error": str(e),
                }
                results.append(error_result)

                if on_error:
                    await on_error(i, step, e)

                if step.on_error == "abort":
                    break
                elif step.on_error == "retry":
                    # TODO: Implement retry logic
                    pass
                # else: continue to next step

        self._current_recipe = None

        if on_complete:
            await on_complete(results)

        return {
            "recipe": recipe.name,
            "total_steps": len(recipe.steps),
            "completed_steps": len(results),
            "results": results,
        }

    def _interpolate(self, text: str) -> str:
        """Interpolate variables in text.

        Args:
            text: Text with {{variable}} placeholders

        Returns:
            Interpolated text
        """
        result = text
        for key, value in self._context.items():
            result = result.replace(f"{{{{{key}}}}}", str(value))
        return result

    def _interpolate_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Interpolate variables in a dictionary."""
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self._interpolate(value)
            elif isinstance(value, dict):
                result[key] = self._interpolate_dict(value)
            else:
                result[key] = value
        return result

    @property
    def is_running(self) -> bool:
        """Check if a recipe is currently running."""
        return self._current_recipe is not None

    @property
    def progress(self) -> tuple[int, int]:
        """Get current progress (current_step, total_steps)."""
        if not self._current_recipe:
            return (0, 0)
        return (self._current_step, len(self._current_recipe.steps))


# Sample recipe generator
def create_sample_recipes(project_root: str | Path) -> None:
    """Create sample recipe files.

    Args:
        project_root: Root directory of the project
    """
    parser = RecipeParser(project_root)
    parser.ensure_recipe_dir()

    # Refactor recipe
    refactor_recipe = Recipe(
        name="refactor",
        description="Refactor code to improve quality and readability",
        author="ChefChat",
        version="1.0.0",
        ingredients=["{{file_path}}"],
        variables={"file_path": "", "style_guide": "PEP 8"},
        steps=[
            RecipeStep(
                name="Analyze Current Code",
                type=StepType.ANALYZE,
                station="sous_chef",
                prompt="Analyze the code in {{file_path}} for improvement opportunities",
                inputs={"file": "{{file_path}}"},
                outputs=["analysis_result"],
            ),
            RecipeStep(
                name="Generate Refactored Code",
                type=StepType.REFACTOR,
                station="line_cook",
                prompt="Refactor following {{style_guide}} guidelines:\n{{analysis_result}}",
                inputs={"original_file": "{{file_path}}"},
                outputs=["refactored_code"],
            ),
            RecipeStep(
                name="Run Tests",
                type=StepType.TEST,
                station="line_cook",
                prompt="Run tests to verify refactoring didn't break functionality",
                inputs={"code": "{{refactored_code}}"},
                outputs=["test_results"],
            ),
            RecipeStep(
                name="Review Changes",
                type=StepType.REVIEW,
                station="sous_chef",
                prompt="Review the refactored code and confirm quality",
                inputs={
                    "original": "{{file_path}}",
                    "refactored": "{{refactored_code}}",
                    "tests": "{{test_results}}",
                },
                outputs=["review_approved"],
            ),
        ],
    )

    try:
        parser.save(refactor_recipe)
    except FileExistsError:
        pass

    # New feature recipe
    feature_recipe = Recipe(
        name="new_feature",
        description="Add a new feature to the codebase",
        author="ChefChat",
        version="1.0.0",
        variables={"feature_name": "", "description": ""},
        steps=[
            RecipeStep(
                name="Design Feature",
                type=StepType.ANALYZE,
                station="sous_chef",
                prompt="Design the implementation plan for: {{feature_name}}\n{{description}}",
                outputs=["design_doc"],
            ),
            RecipeStep(
                name="Verify Dependencies",
                type=StepType.VERIFY,
                station="sommelier",
                prompt="Check if any new dependencies are needed",
                outputs=["dependencies"],
            ),
            RecipeStep(
                name="Implement Feature",
                type=StepType.GENERATE,
                station="line_cook",
                prompt="Implement {{feature_name}} according to:\n{{design_doc}}",
                inputs={"plan": "{{design_doc}}"},
                outputs=["implementation"],
            ),
            RecipeStep(
                name="Write Tests",
                type=StepType.GENERATE,
                station="line_cook",
                prompt="Write tests for {{feature_name}}",
                inputs={"code": "{{implementation}}"},
                outputs=["test_code"],
            ),
            RecipeStep(
                name="Run Tests",
                type=StepType.TEST,
                station="line_cook",
                prompt="Execute the test suite",
                outputs=["test_results"],
            ),
        ],
    )

    try:
        parser.save(feature_recipe)
    except FileExistsError:
        pass
