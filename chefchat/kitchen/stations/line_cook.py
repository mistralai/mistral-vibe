"""ChefChat Line Cook Station - The Code Generation Agent.

The Line Cook is where the actual cooking happens. They:
- Receive PLAN messages from the Sous Chef
- Generate code using the LLM (simulated for now)
- Send progress updates (0-100%) back to the bus
- Report results to The Plate
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from chefchat.kitchen.bus import BaseStation, ChefMessage, KitchenBus

if TYPE_CHECKING:
    from chefchat.kitchen.brain import KitchenBrain


class LineCook(BaseStation):
    """The code generation and execution station.

    Handles:
    - Code generation via LLM
    - Progress updates during work
    - Code delivery to The Plate
    - Result reporting
    """

    def __init__(self, bus: KitchenBus, brain: KitchenBrain) -> None:
        """Initialize the Line Cook station.

        Args:
            bus: The kitchen bus to connect to
            brain: The kitchen brain for LLM operations
        """
        super().__init__("line_cook", bus)
        self.brain = brain
        self._current_task: str | None = None

    async def handle(self, message: ChefMessage) -> None:
        """Process incoming messages.

        Args:
            message: The message to process
        """
        action = message.action

        if action == "PLAN":
            # Implementation request from Sous Chef
            await self._execute_plan(message.payload)

        elif action == "test":
            # Run tests on code
            await self._run_tests(message.payload)

        elif action == "refactor":
            # Refactor existing code
            await self._refactor(message.payload)

        elif action == "FIX_ERRORS":
            # Fix errors from Expeditor (self-healing loop)
            await self._fix_errors(message.payload)

    async def _execute_plan(self, plan: dict) -> None:
        """Execute the implementation plan.

        Args:
            plan: The implementation plan from Sous Chef
        """
        task = plan.get("task", "Unknown Task")
        ticket_id = plan.get("ticket_id", "unknown")

        self._current_task = ticket_id

        # Notify we're starting
        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "cooking",
                "progress": 0,
                "message": "🍳 Firing up the grill...",
            },
        )

        try:
            # Generate code using the Brain with streaming updates
            generated_code = ""
            async for chunk in self.brain.stream_response(
                f"Implement this plan:\n{task}", system=self.brain.CODE_SYSTEM_PROMPT
            ):
                generated_code += chunk
                # Throttle updates to avoid flooding TUI
                if len(generated_code) % 50 == 0:
                    await self.send(
                        recipient="tui",
                        action="STREAM_UPDATE",
                        payload={"content": chunk, "full_content": generated_code},
                    )

            # Complete!
            await self.send(
                recipient="tui",
                action="STATUS_UPDATE",
                payload={
                    "station": self.name,
                    "status": "complete",
                    "progress": 100,
                    "message": "✅ Plated!",
                },
            )

            # Send final code to The Plate
            await self.send(
                recipient="tui",
                action="PLATE_CODE",
                payload={
                    "code": generated_code,
                    "language": "python",
                    "file_path": f"solution_{ticket_id[:5]}.py",
                    "ticket_id": ticket_id,
                },
            )

            # Report completion to Sous Chef
            await self.send(
                recipient="sous_chef",
                action="TASK_COMPLETE",
                payload={
                    "ticket_id": ticket_id,
                    "result": "Code generated successfully",
                },
            )

        except Exception as e:
            await self._send_error(str(e))

    def _generate_code(self, task: str) -> str:
        """Deprecated: Use self.brain.write_code instead."""
        return ""

    async def _fix_errors(self, payload: dict) -> None:
        """Attempt to fix errors reported by Expeditor.

        Part of the self-healing loop.

        Args:
            payload: Error details from Expeditor
        """
        ticket_id = payload.get("ticket_id", "unknown")
        attempt = payload.get("attempt", 1)
        max_attempts = payload.get("max_attempts", 3)
        errors = payload.get("errors", [])
        path = payload.get("path", ".")

        self._current_task = ticket_id

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "cooking",
                "progress": 0,
                "message": f"🔧 Fixing errors (attempt {attempt}/{max_attempts})...",
            },
        )

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": f"🔧 **Line Cook**: Attempting repair {attempt}/{max_attempts}...",
            },
        )

        # Simulate fix attempt
        await asyncio.sleep(1.5)

        # TODO: Connect to LLM to actually fix the errors
        # For now, simulate a fix attempt

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete",
                "progress": 100,
                "message": "✅ Fix applied",
            },
        )

        # Report back to Expeditor
        await self.send(
            recipient="expeditor",
            action="healing_result",
            payload={
                "ticket_id": ticket_id,
                "success": True,  # Simulated success
                "path": path,
            },
        )

        self._current_task = None

    async def _execute_plan(self, payload: dict) -> None:
        """Execute a plan delegated from Sous Chef.

        Sends progress updates every second until complete.

        Args:
            payload: The plan details
        """
        ticket_id = payload.get("ticket_id", "unknown")
        task = payload.get("task", "")

        self._current_task = ticket_id

        # Notify we're starting
        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "cooking",
                "progress": 0,
                "message": "🍳 Firing...",
            },
        )

        # Simulate work with progress updates
        total_steps = 5
        for step in range(total_steps):
            progress = int((step / total_steps) * 100)

            await self.send(
                recipient="tui",
                action="STATUS_UPDATE",
                payload={
                    "station": self.name,
                    "status": "cooking",
                    "progress": progress,
                    "message": f"🔥 Cooking... {progress}%",
                },
            )

            await asyncio.sleep(0.8)  # Simulate work

        # Generate the "code" (simulated LLM output)
        generated_code = self._generate_code(task)

        # Complete!
        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete",
                "progress": 100,
                "message": "✅ Plated!",
            },
        )

        # Send code to The Plate
        await self.send(
            recipient="tui",
            action="PLATE_CODE",
            payload={
                "code": generated_code,
                "language": "python",
                "file_path": f"solution_{ticket_id}.py",
                "ticket_id": ticket_id,
            },
        )

        # Report completion to Sous Chef
        await self.send(
            recipient="sous_chef",
            action="task_complete",
            payload={
                "ticket_id": ticket_id,
                "result": {"code": generated_code, "status": "success"},
            },
        )

        self._current_task = None

    def _generate_code(self, task: str) -> str:
        """Generate code for the task (placeholder for LLM).

        Args:
            task: The task description

        Returns:
            Generated Python code
        """
        # TODO: Connect to actual LLM backend
        # For now, generate a placeholder that shows the task
        safe_task = task[:50].replace('"', '\\"').replace("\n", " ")

        return f'''"""
ChefChat Generated Solution
Task: {safe_task}
"""

from __future__ import annotations


def chef_solution() -> str:
    """Implementation generated by ChefChat's Line Cook.

    Returns:
        Result message
    """
    # TODO: Implement actual logic based on task
    print("👨‍🍳 Oui, Chef!")
    print(f"Processing: {safe_task!r}")

    return "Dish served! 🍽️"


if __name__ == "__main__":
    result = chef_solution()
    print(f"Result: {{result}}")
'''

    async def _run_tests(self, payload: dict) -> None:
        """Run tests on generated code.

        Args:
            payload: The test request details
        """
        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "testing",
                "progress": 50,
                "message": "🧪 Running tests...",
            },
        )

        await asyncio.sleep(1)

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete",
                "progress": 100,
                "message": "✅ Tests passed!",
            },
        )

    async def _refactor(self, payload: dict) -> None:
        """Refactor existing code.

        Args:
            payload: The refactor request details
        """
        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "refactoring",
                "progress": 50,
                "message": "🔄 Refactoring...",
            },
        )

        await asyncio.sleep(1)

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete",
                "progress": 100,
                "message": "✅ Refactored!",
            },
        )
