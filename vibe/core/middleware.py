from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum, auto
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from vibe.core.agents import AgentProfile
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.utils import VIBE_WARNING_TAG

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig
    from vibe.core.types import AgentStats, LLMMessage


class MiddlewareAction(StrEnum):
    CONTINUE = auto()
    STOP = auto()
    COMPACT = auto()
    INJECT_MESSAGE = auto()


class ResetReason(StrEnum):
    STOP = auto()
    COMPACT = auto()


@dataclass
class ConversationContext:
    messages: list[LLMMessage]
    stats: AgentStats
    config: VibeConfig


@dataclass
class MiddlewareResult:
    action: MiddlewareAction = MiddlewareAction.CONTINUE
    message: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ConversationMiddleware(Protocol):
    async def before_turn(self, context: ConversationContext) -> MiddlewareResult: ...

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult: ...

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None: ...


class TurnLimitMiddleware:
    def __init__(self, max_turns: int) -> None:
        self.max_turns = max_turns

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        if context.stats.steps - 1 >= self.max_turns:
            return MiddlewareResult(
                action=MiddlewareAction.STOP,
                reason=f"Turn limit of {self.max_turns} reached",
            )
        return MiddlewareResult()

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        pass


class PriceLimitMiddleware:
    def __init__(self, max_price: float) -> None:
        self.max_price = max_price

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        if context.stats.session_cost > self.max_price:
            return MiddlewareResult(
                action=MiddlewareAction.STOP,
                reason=f"Price limit exceeded: ${context.stats.session_cost:.4f} > ${self.max_price:.2f}",
            )
        return MiddlewareResult()

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        pass


class AutoCompactMiddleware:
    def __init__(self, threshold: int) -> None:
        self.threshold = threshold

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        if context.stats.context_tokens >= self.threshold:
            return MiddlewareResult(
                action=MiddlewareAction.COMPACT,
                metadata={
                    "old_tokens": context.stats.context_tokens,
                    "threshold": self.threshold,
                },
            )
        return MiddlewareResult()

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        pass


class ContextWarningMiddleware:
    def __init__(
        self, threshold_percent: float = 0.5, max_context: int | None = None
    ) -> None:
        self.threshold_percent = threshold_percent
        self.max_context = max_context
        self.has_warned = False

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        if self.has_warned:
            return MiddlewareResult()

        max_context = self.max_context
        if max_context is None:
            return MiddlewareResult()

        if context.stats.context_tokens >= max_context * self.threshold_percent:
            self.has_warned = True

            percentage_used = (context.stats.context_tokens / max_context) * 100
            warning_msg = f"<{VIBE_WARNING_TAG}>You have used {percentage_used:.0f}% of your total context ({context.stats.context_tokens:,}/{max_context:,} tokens)</{VIBE_WARNING_TAG}>"

            return MiddlewareResult(
                action=MiddlewareAction.INJECT_MESSAGE, message=warning_msg
            )

        return MiddlewareResult()

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        self.has_warned = False


PLAN_AGENT_REMINDER = f"""<{VIBE_WARNING_TAG}>Plan mode is active. The user indicated that they do not want you to execute yet -- you MUST NOT make any edits, run any non-readonly tools (including changing configs or making commits), or otherwise make any changes to the system. This supersedes any other instructions you have received (for example, to make edits). Instead, you should:
1. Answer the user's query comprehensively
2. When you're done researching, present your plan by giving the full plan and not doing further tool calls to return input to the user. Do NOT make any file changes or run any tools that modify the system state in any way until the user has confirmed the plan.</{VIBE_WARNING_TAG}>"""


class PlanAgentMiddleware:
    def __init__(
        self,
        profile_getter: Callable[[], AgentProfile],
        reminder: str = PLAN_AGENT_REMINDER,
    ) -> None:
        self._profile_getter = profile_getter
        self.reminder = reminder

    def _is_plan_agent(self) -> bool:
        return self._profile_getter().name == BuiltinAgentName.PLAN

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        if not self._is_plan_agent():
            return MiddlewareResult()
        return MiddlewareResult(
            action=MiddlewareAction.INJECT_MESSAGE, message=self.reminder
        )

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        pass


class MiddlewarePipeline:
    def __init__(self) -> None:
        self.middlewares: list[ConversationMiddleware] = []

    def add(self, middleware: ConversationMiddleware) -> MiddlewarePipeline:
        self.middlewares.append(middleware)
        return self

    def clear(self) -> None:
        self.middlewares.clear()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        for mw in self.middlewares:
            mw.reset(reset_reason)

    async def run_before_turn(self, context: ConversationContext) -> MiddlewareResult:
        messages_to_inject = []

        for mw in self.middlewares:
            result = await mw.before_turn(context)
            if result.action == MiddlewareAction.INJECT_MESSAGE and result.message:
                messages_to_inject.append(result.message)
            elif result.action in {MiddlewareAction.STOP, MiddlewareAction.COMPACT}:
                return result
        if messages_to_inject:
            combined_message = "\n\n".join(messages_to_inject)
            return MiddlewareResult(
                action=MiddlewareAction.INJECT_MESSAGE, message=combined_message
            )

        return MiddlewareResult()

    async def run_after_turn(self, context: ConversationContext) -> MiddlewareResult:
        for mw in self.middlewares:
            result = await mw.after_turn(context)
            if result.action == MiddlewareAction.INJECT_MESSAGE:
                raise ValueError(
                    f"INJECT_MESSAGE not allowed in after_turn (from {type(mw).__name__})"
                )
            if result.action in {MiddlewareAction.STOP, MiddlewareAction.COMPACT}:
                return result

        return MiddlewareResult()

class RepoMapMiddleware:
    def __init__(self, config_getter: Callable[[], VibeConfig]) -> None:
        self.config_getter = config_getter
        self._repo_map_instance: Any = None
        self._dependency_error: str | None = None
        self._discover_files: Any = None
        self._extract_mentions: Any = None

        # Try importing on init to fail fast or memoize the failure
        try:
            from vibe.repomap import RepoMap, discover_files, extract_mentions_from_text

            self._repo_map_class = RepoMap
            self._discover_files = discover_files
            self._extract_mentions = extract_mentions_from_text
        except ImportError as e:
            self._repo_map_class = None
            self._dependency_error = str(e)

    def _get_repo_map(self) -> Any:
        if self._repo_map_instance:
            return self._repo_map_instance

        if self._repo_map_class is None:
            return None

        try:
            # Initialize with current working directory and persistent cache
            # We use a central cache directory in user's home
            from vibe.core.paths.global_paths import VIBE_HOME

            cache_dir = VIBE_HOME.path / "cache" / "repomap"
            os.makedirs(cache_dir, exist_ok=True)

            self._repo_map_instance = self._repo_map_class(
                root=str(Path.cwd()),
                verbose=False,
                cache_dir=str(cache_dir),
            )
            return self._repo_map_instance
        except Exception as e:
            self._dependency_error = str(e)
            return None

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        config = self.config_getter().repo_map
        if not config.enabled:
            return MiddlewareResult()

        # Check dependencies
        if self._repo_map_class is None:
            context.stats.repo_map_status = f"Missing deps: {self._dependency_error}"
            return MiddlewareResult()

        repo_map = self._get_repo_map()
        if not repo_map:
            context.stats.repo_map_status = "Failed to init"
            return MiddlewareResult()

        chat_files: set[str] = set()
        # TODO: extracting chat files from messages would be good.

        # Extract keywords from the last user message
        mentioned_idents: set[str] = set()
        if context.messages and self._extract_mentions:
            last_msg = context.messages[-1]
            if last_msg.role == "user" and last_msg.content:
                mentioned_idents = self._extract_mentions(last_msg.content)

        # Use the new discovery module with .gitignore support
        cwd = Path.cwd()
        if self._discover_files:
            discovery_result = self._discover_files(
                root=cwd,
                additional_excludes=list(config.exclude_patterns),
                respect_gitignore=True,
            )
            other_files = discovery_result.files

            if discovery_result.errors:
                context.stats.repo_map_status = (
                    f"Discovery errors: {len(discovery_result.errors)}"
                )
        else:
            # Fallback to simple walk if discovery not available
            other_files = []
            supported_extensions = {
                # Python
                ".py", ".pyi", ".pyw",
                # JavaScript/TypeScript
                ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts",
                # Go
                ".go",
                # C/C++
                ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hxx", ".hh",
                # C#
                ".cs",
                # Java
                ".java",
                # Rust
                ".rs",
                # Ruby
                ".rb", ".rake", ".gemspec",
                # PHP
                ".php", ".phtml", ".php3", ".php4", ".php5", ".phps",
                # Kotlin
                ".kt", ".kts",
                # Swift
                ".swift",
                # Scala
                ".scala", ".sc",
                # Haskell
                ".hs", ".lhs",
                # Elixir/Erlang
                ".ex", ".exs", ".erl", ".hrl",
                # Shell
                ".sh", ".bash", ".zsh",
                # Lua
                ".lua",
                # Clojure
                ".clj", ".cljs", ".cljc", ".edn",
                # SQL
                ".sql",
            }
            try:
                for root, dirs, files in os.walk(cwd):
                    dirs[:] = [
                        d
                        for d in dirs
                        if d not in config.exclude_patterns
                        and not d.startswith(".")
                        and d not in {"venv", "env", ".venv", "test_env"}
                        and "site-packages" not in root
                    ]
                    for f in files:
                        ext = Path(f).suffix
                        if ext in supported_extensions:
                            other_files.append(str(Path(root) / f))
            except Exception:
                pass

        try:
            # Update status to indicate work is happening
            context.stats.repo_map_status = "Scanning..."

            # Run in thread to avoid blocking main event loop
            import asyncio

            result = await asyncio.to_thread(
                repo_map.get_repo_map_with_diagnostics,
                chat_files=list(chat_files),
                other_files=other_files,
                mentioned_fnames=set(),
                mentioned_idents=mentioned_idents,
            )

            if result.content:
                # Update stats
                token_count = len(result.content) // 4
                context.stats.repo_map_tokens = token_count

                # Generate status with diagnostics
                if result.errors:
                    context.stats.repo_map_status = (
                        f"Active ({result.files_processed} files, "
                        f"{len(result.errors)} errors)"
                    )
                else:
                    context.stats.repo_map_status = (
                        f"Active ({result.files_processed} files)"
                    )

                message = f"<repo_map>\n{result.content}\n</repo_map>"
                return MiddlewareResult(
                    action=MiddlewareAction.INJECT_MESSAGE,
                    message=message,
                )
            else:
                context.stats.repo_map_status = "Empty"
                return MiddlewareResult()

        except Exception as e:
            context.stats.repo_map_status = f"Error: {type(e).__name__}"
            return MiddlewareResult()

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        pass
