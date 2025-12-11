"""ChefChat Expeditor Station - The QA & Testing Agent.

The Expeditor is the final quality checkpoint before food leaves the kitchen.
In ChefChat, they:
- Run tests (pytest) on generated code
- Run linters (ruff) for code quality
- Implement self-healing loop: if tests fail, send back to Line Cook
- Ensure nothing broken reaches the user

"The Expeditor tastes every dish before it leaves the pass."
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from chefchat.kitchen.bus import BaseStation, ChefMessage, KitchenBus, MessagePriority

if TYPE_CHECKING:
    pass


class TasteTestType(Enum):
    """Types of quality checks the Expeditor can run."""

    PYTEST = auto()  # Run pytest
    RUFF = auto()  # Run ruff linter
    PYRIGHT = auto()  # Run pyright type checker
    ALL = auto()  # Run all checks


@dataclass
class TasteTestResult:
    """Result from running a taste test."""

    test_type: TasteTestType
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float

    @property
    def summary(self) -> str:
        """Get a summary of the result."""
        status = "✅ PASSED" if self.passed else "❌ FAILED"
        return f"{self.test_type.name}: {status} (exit code: {self.exit_code})"


class Expeditor(BaseStation):
    """The QA and testing station.

    Runs quality checks and implements self-healing loop:
    - If tests fail, sends code back to Line Cook with error
    - Maximum 3 retry attempts to prevent infinite loops
    - Captures all stderr for debugging
    """

    # Defaults (can be overridden by palate.toml config)
    DEFAULT_MAX_HEALING_ATTEMPTS = 3
    DEFAULT_TIMEOUT = 60  # seconds

    def __init__(self, bus: KitchenBus, project_root: str | Path | None = None) -> None:
        """Initialize the Expeditor station.

        Args:
            bus: The kitchen bus to connect to
            project_root: Root directory for running tests
        """
        super().__init__("expeditor", bus)
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self._healing_attempts: dict[str, int] = {}  # ticket_id -> attempt count
        self._current_test: str | None = None

        # Load config from palate.toml
        from chefchat.config import load_palate_config

        self._config = load_palate_config(self.project_root)
        self.max_healing_attempts = self._config.healing.max_attempts
        self.timeout = self._config.healing.timeout_seconds

    async def handle(self, message: ChefMessage) -> None:
        """Process incoming messages.

        Args:
            message: The message to process
        """
        action = message.action

        if action == "TASTE_TEST":
            await self._run_taste_test(message.payload)

        elif action == "VERIFY_CODE":
            await self._verify_code(message.payload)

        elif action == "LINT_CHECK":
            await self._run_linter(message.payload)

        elif action == "healing_result":
            # Result from Line Cook after fix attempt
            await self._handle_healing_result(message.payload)

    async def _run_taste_test(self, payload: dict) -> None:
        """Run the full taste test suite.

        Args:
            payload: Test configuration
        """
        ticket_id = payload.get("ticket_id", "unknown")
        test_types = payload.get("tests", ["pytest", "ruff"])
        target_path = payload.get("path", ".")

        self._current_test = ticket_id

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "testing",
                "progress": 0,
                "message": "🧪 Starting taste test...",
            },
        )

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": f"🍽️ **Taste Test**: Running quality checks on `{target_path}`...",
            },
        )

        results: list[TasteTestResult] = []
        all_passed = True

        # Run each test type
        for i, test_type_str in enumerate(test_types):
            progress = int((i / len(test_types)) * 80)

            await self.send(
                recipient="tui",
                action="STATUS_UPDATE",
                payload={
                    "station": self.name,
                    "status": "testing",
                    "progress": progress,
                    "message": f"🔍 Running {test_type_str}...",
                },
            )

            test_type = TasteTestType[test_type_str.upper()]
            result = await self._execute_test(test_type, target_path)
            results.append(result)

            if not result.passed:
                all_passed = False

        # Report results
        if all_passed:
            await self._report_success(ticket_id, results)
        else:
            await self._handle_failure(ticket_id, target_path, results)

    async def _execute_test(
        self, test_type: TasteTestType, target_path: str
    ) -> TasteTestResult:
        """Execute a specific test type.

        Args:
            test_type: Type of test to run
            target_path: Path to test

        Returns:
            TasteTestResult with outcome
        """
        import time

        start_time = time.time()

        # Build command based on test type and config
        from chefchat.config import get_lint_command, get_test_command

        if test_type == TasteTestType.PYTEST:
            cmd = get_test_command(self._config, target_path)
        elif test_type == TasteTestType.RUFF:
            cmd = get_lint_command(self._config, target_path)
        elif test_type == TasteTestType.PYRIGHT:
            cmd = ["uv", "run", "pyright", target_path]
        else:
            # ALL: run configured test command
            cmd = get_test_command(self._config, target_path)

        # Run in subprocess
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.project_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout
                )
            except TimeoutError:
                process.kill()
                return TasteTestResult(
                    test_type=test_type,
                    passed=False,
                    exit_code=-1,
                    stdout="",
                    stderr="Test timed out",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            duration_ms = (time.time() - start_time) * 1000
            exit_code = process.returncode or 0

            return TasteTestResult(
                test_type=test_type,
                passed=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                duration_ms=duration_ms,
            )

        except Exception as e:
            return TasteTestResult(
                test_type=test_type,
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    async def _report_success(
        self, ticket_id: str, results: list[TasteTestResult]
    ) -> None:
        """Report successful taste test.

        Args:
            ticket_id: The ticket ID
            results: All test results
        """
        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete",
                "progress": 100,
                "message": "✅ All tests passed!",
            },
        )

        summary_lines = [r.summary for r in results]
        total_time = sum(r.duration_ms for r in results)

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "assistant",
                "content": (
                    "🍽️ **Taste Test Complete!**\n\n"
                    + "\n".join(f"- {s}" for s in summary_lines)
                    + f"\n\nTotal time: {total_time:.0f}ms"
                ),
            },
        )

        # Clear healing attempts for this ticket
        if ticket_id in self._healing_attempts:
            del self._healing_attempts[ticket_id]

        self._current_test = None

    async def _handle_failure(
        self, ticket_id: str, target_path: str, results: list[TasteTestResult]
    ) -> None:
        """Handle failed taste test with self-healing loop.

        Args:
            ticket_id: The ticket ID
            target_path: Path being tested
            results: All test results
        """
        # Check healing attempts
        attempts = self._healing_attempts.get(ticket_id, 0)

        if attempts >= self.max_healing_attempts:
            # Max retries reached - report final failure
            await self._report_final_failure(ticket_id, results, attempts)
            return

        # Increment attempt counter
        self._healing_attempts[ticket_id] = attempts + 1
        current_attempt = attempts + 1

        # Collect errors from failed tests
        errors = []
        for result in results:
            if not result.passed:
                error_text = result.stderr or result.stdout
                errors.append(
                    f"**{result.test_type.name}**:\n```\n{error_text[:500]}\n```"
                )

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "error",
                "progress": 0,
                "message": f"🔄 Healing attempt {current_attempt}/{self.max_healing_attempts}",
            },
        )

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": (
                    f"⚠️ **Taste test failed** - Attempt {current_attempt}/{self.max_healing_attempts}\n\n"
                    f"Sending back to Line Cook for repairs..."
                ),
            },
        )

        # Send to Line Cook for fixing
        await self.send(
            recipient="line_cook",
            action="FIX_ERRORS",
            payload={
                "ticket_id": ticket_id,
                "path": target_path,
                "attempt": current_attempt,
                "max_attempts": self.MAX_HEALING_ATTEMPTS,
                "errors": errors,
                "raw_results": [
                    {
                        "type": r.test_type.name,
                        "passed": r.passed,
                        "stdout": r.stdout[:1000],
                        "stderr": r.stderr[:1000],
                    }
                    for r in results
                ],
            },
            priority=MessagePriority.HIGH,
        )

    async def _report_final_failure(
        self, ticket_id: str, results: list[TasteTestResult], attempts: int
    ) -> None:
        """Report final failure after max healing attempts.

        Args:
            ticket_id: The ticket ID
            results: All test results
            attempts: Number of attempts made
        """
        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "error",
                "progress": 0,
                "message": f"❌ Failed after {attempts} attempts",
            },
        )

        failed_tests = [r for r in results if not r.passed]

        await self.send(
            recipient="tui",
            action="LOG_MESSAGE",
            payload={
                "type": "system",
                "content": (
                    f"❌ **Taste test failed** after {attempts} healing attempts.\n\n"
                    f"Failed checks: {', '.join(r.test_type.name for r in failed_tests)}\n\n"
                    f"*Manual intervention required, Chef.*"
                ),
            },
        )

        # Show errors in The Plate
        error_output = "\n\n".join(
            f"# {r.test_type.name} ERRORS\n{r.stderr or r.stdout}" for r in failed_tests
        )

        await self.send(
            recipient="tui",
            action="PLATE_CODE",
            payload={
                "code": error_output,
                "language": "text",
                "file_path": f"taste_test_errors_{ticket_id}.txt",
            },
        )

        # Clean up
        if ticket_id in self._healing_attempts:
            del self._healing_attempts[ticket_id]
        self._current_test = None

    async def _handle_healing_result(self, payload: dict) -> None:
        """Handle result from Line Cook after fix attempt.

        Args:
            payload: The fix result
        """
        ticket_id = payload.get("ticket_id", "unknown")
        success = payload.get("success", False)

        if success:
            # Re-run tests on fixed code
            await self._run_taste_test({
                "ticket_id": ticket_id,
                "tests": ["pytest", "ruff"],
                "path": payload.get("path", "."),
            })
        else:
            # Fix failed, try again or give up
            attempts = self._healing_attempts.get(ticket_id, 0)
            if attempts >= self.max_healing_attempts:
                await self.send(
                    recipient="tui",
                    action="LOG_MESSAGE",
                    payload={
                        "type": "system",
                        "content": "❌ **86'd!** Line Cook couldn't fix the errors.",
                    },
                )

    async def _verify_code(self, payload: dict) -> None:
        """Quick verification of code without full test suite.

        Args:
            payload: Verification request
        """
        target_path = payload.get("path", ".")

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "testing",
                "progress": 50,
                "message": "🔍 Quick verification...",
            },
        )

        # Just run ruff for quick check
        result = await self._execute_test(TasteTestType.RUFF, target_path)

        if result.passed:
            await self.send(
                recipient="tui",
                action="LOG_MESSAGE",
                payload={
                    "type": "assistant",
                    "content": "✅ **Quick verification passed!** No lint errors.",
                },
            )
        else:
            await self.send(
                recipient="tui",
                action="LOG_MESSAGE",
                payload={
                    "type": "system",
                    "content": f"⚠️ **Lint issues found:**\n```\n{result.stdout[:500]}\n```",
                },
            )

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete" if result.passed else "error",
                "progress": 100,
                "message": "✅ Done" if result.passed else "⚠️ Issues found",
            },
        )

    async def _run_linter(self, payload: dict) -> None:
        """Run just the linter.

        Args:
            payload: Linter request
        """
        target_path = payload.get("path", ".")

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "testing",
                "progress": 50,
                "message": "🔍 Running linter...",
            },
        )

        result = await self._execute_test(TasteTestType.RUFF, target_path)

        await self.send(
            recipient="tui",
            action="STATUS_UPDATE",
            payload={
                "station": self.name,
                "status": "complete" if result.passed else "error",
                "progress": 100,
                "message": result.summary,
            },
        )

        if not result.passed:
            await self.send(
                recipient="tui",
                action="PLATE_CODE",
                payload={
                    "code": result.stdout,
                    "language": "text",
                    "file_path": "lint_results.txt",
                },
            )
