"""Hook executor module.

Responsible for executing hook commands as shell processes,
passing JSON input via stdin and parsing JSON output from stdout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from pydantic import ValidationError

from vibe.core.hooks.types import (
    HookConfig,
    HookInput,
    HookOutput,
    HookResult,
    PostToolUseHookOutput,
    PreToolUseHookOutput,
    SessionEndHookOutput,
    SessionStartHookOutput,
    UserPromptSubmitHookOutput,
)

logger = logging.getLogger(__name__)

# Exit code 2 signals a blocking error from hooks
EXIT_CODE_BLOCKING_ERROR = 2


def _get_output_class(hook_input: HookInput) -> type[HookOutput]:
    """Determine the appropriate output class based on the input type."""
    from vibe.core.hooks.types import (
        PostToolUseHookInput,
        PreToolUseHookInput,
        SessionEndHookInput,
        SessionStartHookInput,
        UserPromptSubmitHookInput,
    )

    if isinstance(hook_input, PreToolUseHookInput):
        return PreToolUseHookOutput
    elif isinstance(hook_input, PostToolUseHookInput):
        return PostToolUseHookOutput
    elif isinstance(hook_input, SessionStartHookInput):
        return SessionStartHookOutput
    elif isinstance(hook_input, SessionEndHookInput):
        return SessionEndHookOutput
    elif isinstance(hook_input, UserPromptSubmitHookInput):
        return UserPromptSubmitHookOutput
    else:
        return HookOutput


async def execute_hook(
    hook: HookConfig,
    hook_input: HookInput,
    cwd: str | None = None,
) -> HookResult:
    """Execute a single hook command.

    Args:
        hook: The hook configuration.
        hook_input: The input data to pass to the hook via stdin.
        cwd: Working directory for the hook command.

    Returns:
        HookResult with the execution details and parsed output.
    """
    start_time = time.perf_counter()
    effective_cwd = cwd or os.getcwd()

    input_json = hook_input.model_dump_json()

    try:
        env = os.environ.copy()
        env["VIBE_HOOK_EVENT"] = hook_input.hook_event_name
        env["VIBE_SESSION_ID"] = hook_input.session_id

        process = await asyncio.create_subprocess_shell(
            hook.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=effective_cwd,
            env=env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=input_json.encode()),
                timeout=hook.timeout,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            execution_time = time.perf_counter() - start_time
            return HookResult(
                hook_config=hook,
                output=HookOutput(),
                execution_time=execution_time,
                exit_code=-1,
                error=f"Hook timed out after {hook.timeout}s",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = process.returncode or 0
        execution_time = time.perf_counter() - start_time

        if exit_code == EXIT_CODE_BLOCKING_ERROR:
            return HookResult(
                hook_config=hook,
                output=HookOutput.model_validate({"continue": False}),
                execution_time=execution_time,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                error=stderr or f"Hook signaled blocking error (exit code {EXIT_CODE_BLOCKING_ERROR})",
            )

        if exit_code != 0:
            logger.warning(
                f"Hook '{hook.command}' exited with code {exit_code}: {stderr}"
            )
            return HookResult(
                hook_config=hook,
                output=HookOutput(),
                execution_time=execution_time,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                error=stderr or f"Hook exited with code {exit_code}",
            )

        output_class = _get_output_class(hook_input)
        output = _parse_hook_output(stdout, output_class)

        return HookResult(
            hook_config=hook,
            output=output,
            execution_time=execution_time,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )

    except Exception as e:
        execution_time = time.perf_counter() - start_time
        logger.error(f"Error executing hook '{hook.command}': {e}")
        return HookResult(
            hook_config=hook,
            output=HookOutput(),
            execution_time=execution_time,
            exit_code=-1,
            error=str(e),
        )


def _parse_hook_output[T: HookOutput](stdout: str, output_class: type[T]) -> T:
    """Parse hook stdout as JSON and validate against output model.

    Args:
        stdout: The raw stdout from the hook command.
        output_class: The Pydantic model class to validate against.

    Returns:
        Validated output model instance.
    """
    if not stdout:
        return output_class()

    try:
        data = json.loads(stdout)
        return output_class.model_validate(data)
    except json.JSONDecodeError:
        logger.debug(f"Hook output is not valid JSON, using defaults: {stdout[:100]}")
        return output_class()
    except ValidationError as e:
        logger.warning(f"Hook output validation failed: {e}")
        return output_class()


async def execute_hooks_parallel(
    hooks: list[HookConfig],
    hook_input: HookInput,
    cwd: str | None = None,
) -> list[HookResult]:
    """Execute multiple hooks in parallel.

    Args:
        hooks: List of hook configurations to execute.
        hook_input: The input data to pass to each hook.
        cwd: Working directory for the hook commands.

    Returns:
        List of HookResult instances, one for each hook.
    """
    if not hooks:
        return []

    tasks = [execute_hook(hook, hook_input, cwd) for hook in hooks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    hook_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Hook execution failed: {result}")
            hook_results.append(
                HookResult(
                    hook_config=hooks[i],
                    output=HookOutput(),
                    execution_time=0.0,
                    exit_code=-1,
                    error=str(result),
                )
            )
        else:
            hook_results.append(result)

    return hook_results
