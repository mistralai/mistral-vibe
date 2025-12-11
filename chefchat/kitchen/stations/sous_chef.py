"""ChefChat Sous Chef Station - The Planning & Orchestration Agent.

The Sous Chef is the Head Chef's right hand. They:
- Receive user requests (NEW_TICKET) and break them into tasks
- Delegate work to the Line Cook and Sommelier (PLAN)
- Handle `/chef prep` command (scan codebase into Knowledge Graph)
- Handle `/chef cook <recipe>` command (execute YAML workflows)
- Coordinate the overall execution flow
- Report progress back to the TUI
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from chefchat.kitchen.bus import BaseStation, ChefMessage, KitchenBus, MessagePriority

if TYPE_CHECKING:
    from chefchat.pantry.ingredients import IngredientsManager
    from chefchat.pantry.recipes import RecipeExecutor


class SousChef(BaseStation):
    """The planning and orchestration station.

    Handles:
    - Task breakdown ("Mise en place")
    - Work delegation to other stations
    - Knowledge Graph management (/chef prep)
    - Recipe execution (/chef cook)
    - Progress aggregation
    - Error recovery coordination
    """

    def __init__(self, bus: KitchenBus, project_root: str | Path | None = None) -> None:
        """Initialize the Sous Chef station.

        Args:
            bus: The kitchen bus to connect to
            project_root: Root directory for Knowledge Graph and Recipes
        """
        super().__init__("sous_chef", bus)
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self._current_ticket: str | None = None
        self._pending_tasks: list[dict] = []
        self._ingredients: IngredientsManager | None = None
        self._recipe_executor: RecipeExecutor | None = None

    async def handle(self, message: ChefMessage) -> None:
        """Process incoming messages.

        Args:
            message: The message to process
        """
        action = message.action

        if action == "NEW_TICKET":
            # New user request - check if it's a chef command
            request = message.payload.get("request", "")

            if request.lower().startswith("/chef "):
                await self._handle_chef_command(message, request)
            else:
                await self._process_new_ticket(message)

        elif action == "task_complete":
            await self._handle_completion(message)

        elif action == "task_error":
            await self._handle_error(message)

        elif action == "status_request":
            await self._report_status(message.sender)

    async def _handle_chef_command(self, message: ChefMessage, command: str) -> None:
        """Handle /chef commands.

        Args:
            message: The original message
            command: The full command string
        """
        parts = command.split()
        if len(parts) < 2:
            await self._send_error("Usage: /chef <prep|cook|recipes>")
            return

        subcommand = parts[1].lower()

        if subcommand == "prep":
            await self._do_mise_en_place()

        elif subcommand == "cook":
            if len(parts) < 3:
                await self._send_error("Usage: /chef cook <recipe_name>")
                return
            recipe_name = parts[2]
            await self._cook_recipe(recipe_name)

        elif subcommand == "taste":
            # Run taste test (QA) via Expeditor
            target_path = parts[2] if len(parts) > 2 else "."
            await self._run_taste_test(target_path)

        elif subcommand == "recipes":
            await self._list_recipes()

        elif subcommand == "status":
            await self._report_graph_status()

        elif subcommand == "undo":
            await self._undo_changes()

        elif subcommand == "critic":
            target_path = parts[2] if len(parts) > 2 else "."
            await self._roast_code(target_path)

        else:
            await self._send_error(
                f"Unknown chef command: {subcommand}\n"
                "Available: prep, cook, taste, recipes, status, undo, critic"
            )

    async def _run_taste_test(self, target_path: str = ".") -> None:
        """Run taste test (QA) via the Expeditor.

        Args:
            target_path: Path to test
        """
        from uuid import uuid4

        ticket_id = str(uuid4())[:8]

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": f"🍽️ **Taste Test**: Sending to Expeditor for QA on `{target_path}`...",
            },
        )

        # Send to Expeditor for testing
        await self.send(
            recipient="expeditor",
            action="TASTE_TEST",
            payload={
                "ticket_id": ticket_id,
                "path": target_path,
                "tests": ["pytest", "ruff"],
            },
            priority=MessagePriority.HIGH,
        )

    async def _do_mise_en_place(self) -> None:
        """Scan the codebase and build the Knowledge Graph.

        'Mise en place' - everything in its place.
        """
        from chefchat.pantry.ingredients import IngredientsManager

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "planning",
                "progress": 0,
                "message": "🔪 Mise en place...",
            },
        )

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": f"📋 **Mise en place**: Scanning codebase at `{self.project_root}`...",
            },
        )

        # Initialize and scan
        self._ingredients = IngredientsManager(self.project_root)

        # Scan in a thread to avoid blocking
        loop = asyncio.get_event_loop()
        stats = await loop.run_in_executor(None, self._ingredients.scan)

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete",
                "progress": 100,
                "message": "✅ Prep complete!",
            },
        )

        # Report results
        summary = (
            f"📊 **Knowledge Graph Ready**\n\n"
            f"- Files: {stats['files']}\n"
            f"- Classes: {stats['classes']}\n"
            f"- Functions: {stats['functions']}\n"
            f"- Methods: {stats['methods']}\n"
            f"- Imports: {stats['imports']}\n"
            f"- Relationships: {stats['edges']}"
        )

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={"type": "assistant", "content": summary},
        )

        # Save the graph
        graph_path = self.project_root / ".chef" / "knowledge_graph.json"
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        self._ingredients.save(graph_path)

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={"type": "system", "content": f"💾 Graph saved to `{graph_path}`"},
        )

    async def _cook_recipe(self, recipe_name: str) -> None:
        """Execute a recipe from the .chef/recipes folder.

        Args:
            recipe_name: Name of the recipe to cook
        """
        from chefchat.pantry.recipes import RecipeExecutor, RecipeParser

        parser = RecipeParser(self.project_root)

        # Check if recipe exists
        available = parser.list_recipes()
        if recipe_name not in available:
            await self._send_error(
                f"Recipe not found: `{recipe_name}`\n"
                f"Available recipes: {', '.join(available) if available else 'None (run `/chef recipes` to create samples)'}"
            )
            return

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": f"🍳 **Cooking**: Loading recipe `{recipe_name}`...",
            },
        )

        try:
            recipe = parser.load(recipe_name)
        except Exception as e:
            await self._send_error(f"Failed to load recipe: {e}")
            return

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "assistant",
                "content": (
                    f"📜 **Recipe: {recipe.name}**\n\n"
                    f"{recipe.description}\n\n"
                    f"Steps: {len(recipe.steps)}"
                ),
            },
        )

        # Execute the recipe
        executor = RecipeExecutor(parser)

        async def on_step(index: int, step: any, total: int) -> None:
            progress = int((index / total) * 100)
            await self.send(
                recipient="tui",
                action="STATUS_UPDATE",
                payload={
                    "station": self.name,
                    "status": "cooking",
                    "progress": progress,
                    "message": f"Step {index + 1}/{total}: {step.name}",
                },
            )
            await self.send(
                recipient="tui",
                action="LOG_MESSAGE",
                payload={
                    "type": "system",
                    "content": f"🔥 Step {index + 1}: **{step.name}**",
                },
            )

        async def on_complete(results: list) -> None:
            await self.send(
                recipient="tui",
                action="STATUS_UPDATE",
                payload={
                    "station": self.name,
                    "status": "complete",
                    "progress": 100,
                    "message": "✅ Recipe complete!",
                },
            )

        result = await executor.execute(
            recipe_name, on_step=on_step, on_complete=on_complete
        )

        # Report results
        completed = sum(1 for r in result["results"] if r["status"] == "success")
        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "assistant",
                "content": (
                    f"🍽️ **{recipe.name} complete!**\n\n"
                    f"Completed {completed}/{result['total_steps']} steps."
                ),
            },
        )

    async def _list_recipes(self) -> None:
        """List available recipes or create samples if none exist."""
        from chefchat.pantry.recipes import RecipeParser, create_sample_recipes

        parser = RecipeParser(self.project_root)
        available = parser.list_recipes()

        if not available:
            # Create sample recipes
            await self.send(
                recipient="tui",
                action="LOG_MESSAGE",
                payload={
                    "type": "system",
                    "content": "📝 No recipes found. Creating samples...",
                },
            )
            create_sample_recipes(self.project_root)
            available = parser.list_recipes()

        recipe_list = "\n".join(
            f"- `{r}`: {parser.get_recipe_info(r)['description']}" for r in available
        )

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "assistant",
                "content": (
                    f"📜 **Available Recipes**\n\n"
                    f"{recipe_list}\n\n"
                    f"*Cook with: `/chef cook <name>`*"
                ),
            },
        )

    async def _report_graph_status(self) -> None:
        """Report Knowledge Graph status."""
        if not self._ingredients:
            await self.send(
                recipient="tui",
                action="LOG_MESSAGE",
                payload={
                    "type": "system",
                    "content": "📊 Knowledge Graph not loaded. Run `/chef prep` first.",
                },
            )
            return

        summary = self._ingredients.get_summary()

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "assistant",
                "content": (
                    f"📊 **Knowledge Graph Status**\n\n"
                    f"- Root: `{summary['root_path']}`\n"
                    f"- Nodes: {summary['total_nodes']}\n"
                    f"- Edges: {summary['total_edges']}\n"
                    f"- Files: {summary['by_type'].get('FILE', 0)}\n"
                    f"- Classes: {summary['by_type'].get('CLASS', 0)}\n"
                    f"- Functions: {summary['by_type'].get('FUNCTION', 0)}"
                ),
            },
        )

    async def _send_error(self, message: str) -> None:
        """Send an error message to the TUI.

        Args:
            message: Error message
        """
        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={"type": "system", "content": f"❌ {message}"},
        )

    async def _undo_changes(self) -> None:
        """Undo the last ChefChat changes using git stash.

        Restores to the state before the last AI-made changes.
        """
        from chefchat.kitchen.mise_en_place import restore_snapshot

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "planning",
                "progress": 50,
                "message": "⏪ Restoring previous state...",
            },
        )

        result = await restore_snapshot(self.project_root)

        status = "complete" if result.success else "error"
        icon = "✅" if result.success else "❌"

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": status,
                "progress": 100,
                "message": f"{icon} {result.message}",
            },
        )

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "assistant" if result.success else "system",
                "content": f"⏪ **Undo**: {result.message}",
            },
        )

    async def _roast_code(self, target_path: str = ".") -> None:
        """Generate a sarcastic code review (Critic mode).

        Args:
            target_path: Path to the file to roast
        """
        from pathlib import Path

        from chefchat.kitchen.brain import KitchenBrain

        target = Path(target_path)
        if not target.is_absolute():
            target = self.project_root / target

        if not target.exists():
            await self._send_error(f"File not found: `{target_path}`")
            return

        if target.is_dir():
            await self._send_error(
                "The Critic reviews files, not directories. "
                "Specify a file path like `/chef critic path/to/file.py`"
            )
            return

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "planning",
                "progress": 25,
                "message": "🔪 The Critic is reading your code...",
            },
        )

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": f"🔥 **The Critic**: Reviewing `{target_path}`...",
            },
        )

        try:
            code = target.read_text()
        except Exception as e:
            await self._send_error(f"Could not read file: {e}")
            return

        # Initialize brain and get roast
        brain = KitchenBrain()

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "cooking",
                "progress": 60,
                "message": "🔥 Preparing brutal honesty...",
            },
        )

        roast = await brain.roast_code(code, target_path)

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete",
                "progress": 100,
                "message": "🍽️ Review complete!",
            },
        )

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "assistant",
                "content": f"🔥 **The Critic on `{target.name}`**:\n\n{roast}",
            },
        )

    async def _process_new_ticket(self, message: ChefMessage) -> None:
        """Process a new ticket from the user (non-chef-command).

        Args:
            message: The NEW_TICKET message
        """
        ticket_id = message.payload.get("ticket_id", str(uuid4())[:8])
        request = message.payload.get("request", "")

        self._current_ticket = ticket_id

        # Notify UI that we're starting to plan
        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "planning",
                "progress": 0,
                "message": "📋 Analyzing order...",
            },
        )

        # Simulate planning time
        await asyncio.sleep(0.5)

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "planning",
                "progress": 50,
                "message": "🔪 Mise en place...",
            },
        )

        await asyncio.sleep(0.5)

        # Plan complete - update UI
        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete",
                "progress": 100,
                "message": "✅ Order planned",
            },
        )

        # Log to ticket rail
        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": f"📋 **Ticket #{ticket_id}** received. Delegating to Line Cook...",
            },
        )

        # Delegate to Line Cook with PLAN
        await self.send(
            recipient="line_cook",
            action="PLAN",
            payload={
                "ticket_id": ticket_id,
                "task": request,
                "plan": {"type": "implement", "description": request},
            },
            priority=MessagePriority.HIGH,
        )

    async def _handle_completion(self, message: ChefMessage) -> None:
        """Handle task completion from a station."""
        sender = message.sender

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "assistant",
                "content": f"🍳 **Order up!** Task completed by {sender}.",
            },
        )

        self._current_ticket = None

    async def _handle_error(self, message: ChefMessage) -> None:
        """Handle errors from other stations."""
        error = message.payload.get("error", "Unknown error")
        sender = message.sender

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": f"❌ **86'd!** Error from {sender}: {error}",
            },
        )

        self._current_ticket = None

    async def _report_status(self, requester: str) -> None:
        """Report current status to requester."""
        await self.send(
            recipient=requester,
            action="status_response",
            payload={
                "station": self.name,
                "current_ticket": self._current_ticket,
                "pending_tasks": len(self._pending_tasks),
                "status": "ready" if not self._current_ticket else "busy",
                "graph_loaded": self._ingredients is not None,
            },
        )
