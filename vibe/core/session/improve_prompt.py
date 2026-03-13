from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_loader import MESSAGES_FILENAME, SessionLoader
from vibe.core.types import LLMMessage, Role
from vibe.core.utils import CANCELLATION_TAG, TOOL_ERROR_TAG, TaggedText


DEFAULT_IMPROVE_SESSION_LIMIT = 50
AGGREGATE_LIMIT = 8
FRICTION_LIMIT = 8
PREVIEW_CHAR_LIMIT = 110
PHRASE_CHAR_LIMIT = 120
NO_PREVIEW = "(no user preview recorded)"


@dataclass(slots=True)
class SessionAnalysis:
    created_at: str
    preview: str = NO_PREVIEW
    cwd: str | None = None
    user_messages: int = 0
    assistant_messages: int = 0
    reasoning_messages: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    tool_cancellations: int = 0
    bash_calls: int = 0
    failed_bash_calls: int = 0
    tool_counts: Counter[str] = field(default_factory=Counter)
    failed_tool_counts: Counter[str] = field(default_factory=Counter)
    bash_command_counts: Counter[str] = field(default_factory=Counter)
    failed_bash_command_counts: Counter[str] = field(default_factory=Counter)
    assistant_phrase_samples: list[str] = field(default_factory=list)

    @classmethod
    def from_session(
        cls, session_dir: Path, messages: list[LLMMessage], metadata: dict[str, Any]
    ) -> SessionAnalysis:
        environment = metadata.get("environment", {})
        cwd = (
            environment.get("working_directory")
            if isinstance(environment, dict)
            else None
        )
        analysis = cls(
            created_at=str(
                metadata.get("end_time")
                or metadata.get("start_time")
                or session_dir.name
            ),
            cwd=shorten_path(Path(cwd)) if isinstance(cwd, str) and cwd else None,
        )
        fallback_preview = metadata.get("title")
        bash_calls_by_id: dict[str, str] = {}
        skip_turn = False

        for message in messages:
            match message.role:
                case Role.user:
                    if (
                        text := extract_message_text(message)
                    ) is not None and is_improve_generated_prompt(text):
                        skip_turn = True
                        continue
                    skip_turn = False
                    analysis.user_messages += 1
                    if (
                        analysis.preview == NO_PREVIEW
                        and text is not None
                    ):
                        analysis.preview = shorten(text, PREVIEW_CHAR_LIMIT)
                case Role.assistant:
                    if skip_turn:
                        continue
                    analysis.assistant_messages += 1
                    if message.reasoning_content:
                        analysis.reasoning_messages += 1
                    if (
                        sample := assistant_phrase_sample(message.content or "")
                    ) is not None:
                        analysis.assistant_phrase_samples.append(sample)
                    for tool_call in message.tool_calls or []:
                        tool_name = tool_call.function.name or "unknown"
                        analysis.tool_calls += 1
                        analysis.tool_counts[tool_name] += 1
                        if tool_name != "bash":
                            continue
                        if (
                            command := summarize_bash_command(
                                tool_call.function.arguments
                            )
                        ) is None:
                            continue
                        analysis.bash_calls += 1
                        analysis.bash_command_counts[command] += 1
                        if tool_call.id:
                            bash_calls_by_id[tool_call.id] = command
                case Role.tool:
                    if skip_turn:
                        continue
                    if (
                        tag := tool_message_tag(message.content or "")
                    ) == CANCELLATION_TAG:
                        analysis.tool_cancellations += 1
                        continue
                    if tag != TOOL_ERROR_TAG:
                        continue
                    tool_name = message.name or "unknown"
                    analysis.tool_failures += 1
                    analysis.failed_tool_counts[tool_name] += 1
                    if tool_name != "bash":
                        continue
                    analysis.failed_bash_calls += 1
                    if (
                        message.tool_call_id
                        and (command := bash_calls_by_id.get(message.tool_call_id))
                    ):
                        analysis.failed_bash_command_counts[command] += 1
                case _:
                    continue

        if analysis.preview == NO_PREVIEW and isinstance(fallback_preview, str):
            analysis.preview = shorten(fallback_preview, PREVIEW_CHAR_LIMIT)

        return analysis

    def friction_score(self) -> int:
        return (
            self.tool_failures * 4
            + self.tool_cancellations * 3
            + self.failed_bash_calls * 2
            + max(self.tool_calls - 8, 0)
        )

    def has_signal(self) -> bool:
        return any(
            (
                self.user_messages,
                self.assistant_messages,
                self.tool_calls,
                self.tool_failures,
                self.tool_cancellations,
            )
        )

    def summary_line(self) -> str:
        line = (
            f"- {self.created_at} | {self.preview} | users {self.user_messages}, "
            f"assistant {self.assistant_messages}, reasoning {self.reasoning_messages}, "
            f"tools {self.tool_calls}, failures {self.tool_failures}, "
            f"cancellations {self.tool_cancellations}, bash failures {self.failed_bash_calls}"
        )
        if self.cwd is not None:
            line += f" | cwd {self.cwd}"
        return line

    def friction_line(self) -> str:
        reasons: list[str] = []
        if self.tool_failures > 0:
            reasons.append(f"tool failures {self.tool_failures}")
        if self.tool_cancellations > 0:
            reasons.append(f"tool cancellations {self.tool_cancellations}")
        if self.failed_bash_calls > 0:
            reasons.append(f"bash failures {self.failed_bash_calls}")
        if not reasons and self.tool_calls > 0:
            reasons.append(f"tool calls {self.tool_calls}")

        line = (
            f"- score {self.friction_score()} | {self.created_at} | {self.preview} | "
            f"{', '.join(reasons) if reasons else 'no strong friction signal'}"
        )
        if top_failed := format_count_entries(top_counts(self.failed_tool_counts, 3)):
            line += f" | failed tools: {top_failed}"
        if top_failed_bash := format_count_entries(
            top_counts(self.failed_bash_command_counts, 2)
        ):
            line += f" | failed bash: {top_failed_bash}"
        return line


@dataclass(slots=True)
class ImproveDigest:
    sessions: list[SessionAnalysis]
    total_user_messages: int
    total_assistant_messages: int
    total_reasoning_messages: int
    total_tool_calls: int
    total_tool_failures: int
    total_tool_cancellations: int
    total_bash_calls: int
    total_failed_bash_calls: int
    top_tools: list[tuple[str, int]]
    top_failed_tools: list[tuple[str, int]]
    top_bash_commands: list[tuple[str, int]]
    top_failed_bash_commands: list[tuple[str, int]]
    top_cwds: list[tuple[str, int]]
    repeated_phrases: list[tuple[str, int]]

    @classmethod
    def from_sessions(cls, sessions: list[SessionAnalysis]) -> ImproveDigest:
        tool_counts: Counter[str] = Counter()
        failed_tool_counts: Counter[str] = Counter()
        bash_command_counts: Counter[str] = Counter()
        failed_bash_command_counts: Counter[str] = Counter()
        cwd_counts: Counter[str] = Counter()
        phrase_counts: dict[str, tuple[str, int]] = {}

        for session in sessions:
            tool_counts.update(session.tool_counts)
            failed_tool_counts.update(session.failed_tool_counts)
            bash_command_counts.update(session.bash_command_counts)
            failed_bash_command_counts.update(session.failed_bash_command_counts)
            if session.cwd is not None:
                cwd_counts[session.cwd] += 1
            for sample in session.assistant_phrase_samples:
                if (key := repetition_key(sample)) is None:
                    continue
                current_sample, count = phrase_counts.get(key, (sample, 0))
                phrase_counts[key] = (current_sample, count + 1)

        repeated_phrases = sorted(
            (
                (sample, count)
                for sample, count in phrase_counts.values()
                if count > 1
            ),
            key=lambda entry: (-entry[1], entry[0]),
        )[:AGGREGATE_LIMIT]

        return cls(
            sessions=sessions,
            total_user_messages=sum(session.user_messages for session in sessions),
            total_assistant_messages=sum(
                session.assistant_messages for session in sessions
            ),
            total_reasoning_messages=sum(
                session.reasoning_messages for session in sessions
            ),
            total_tool_calls=sum(session.tool_calls for session in sessions),
            total_tool_failures=sum(session.tool_failures for session in sessions),
            total_tool_cancellations=sum(
                session.tool_cancellations for session in sessions
            ),
            total_bash_calls=sum(session.bash_calls for session in sessions),
            total_failed_bash_calls=sum(
                session.failed_bash_calls for session in sessions
            ),
            top_tools=top_counts(tool_counts, AGGREGATE_LIMIT),
            top_failed_tools=top_counts(failed_tool_counts, AGGREGATE_LIMIT),
            top_bash_commands=top_counts(bash_command_counts, AGGREGATE_LIMIT),
            top_failed_bash_commands=top_counts(
                failed_bash_command_counts, AGGREGATE_LIMIT
            ),
            top_cwds=top_counts(cwd_counts, 5),
            repeated_phrases=repeated_phrases,
        )

    def render_prompt(self, requested_limit: int) -> str:
        lines = [
            (
                f"Analyze this digest of my last {len(self.sessions)} interactive "
                "Mistral Vibe sessions and tell me how Vibe should improve."
            ),
            "",
            "Focus on:",
            (
                "- Concrete friction patterns and the smallest prompt or "
                "default-behavior change that would address each one."
            ),
            (
                "- Explicit suggestions to try next, including prompt wording, "
                "default behaviors, and automation when the evidence is strong."
            ),
            "- Repeated explanations or phrasing that should be cut, shortened, or standardized.",
            "- Evidence-driven recommendations only. If support is weak, say so.",
            (
                "- Only suggest a new skill, slash command, hook, or helper script "
                "when the same workflow or friction shows up in at least two sessions."
            ),
            "- Keep the response concise: use at most 3 bullets per section.",
            "- In every section, make each bullet start with the change.",
            (
                "- Do not restate the digest; cite only the minimum evidence needed "
                "to justify each recommendation."
            ),
            "",
            "Respond with these sections:",
            "1. Friction to address",
            "2. Suggestions to try",
            "3. Repetition to remove",
            "4. Ship next",
            "",
            "## Aggregate stats",
            f"- Sessions analyzed: {len(self.sessions)}",
            (
                f"- Messages: user {self.total_user_messages}, assistant "
                f"{self.total_assistant_messages}, reasoning "
                f"{self.total_reasoning_messages}"
            ),
            (
                f"- Workload: tool calls {self.total_tool_calls}, tool failures "
                f"{self.total_tool_failures}, tool cancellations "
                f"{self.total_tool_cancellations}"
            ),
            (
                f"- Bash usage: commands {self.total_bash_calls}, failed bash "
                f"commands {self.total_failed_bash_calls}"
            ),
        ]
        if len(self.sessions) < requested_limit:
            lines.append(
                f"- Sample size note: only {len(self.sessions)} earlier sessions were available."
            )

        lines.extend(
            [
                "",
                "## Most used tools",
                *render_count_section(self.top_tools),
                "",
                "## Most failed tools",
                *render_count_section(self.top_failed_tools),
                "",
                "## Most used bash commands",
                *render_count_section(self.top_bash_commands),
                "",
                "## Most failed bash commands",
                *render_count_section(self.top_failed_bash_commands),
                "",
                "## Common working directories",
                *render_count_section(self.top_cwds),
                "",
                "## Repeated assistant phrases",
            ]
        )
        if not self.repeated_phrases:
            lines.append(
                "- No strongly repeated assistant phrasing was detected in the local digest."
            )
        else:
            lines.extend(
                f"- {sample} (seen {count} times)"
                for sample, count in self.repeated_phrases
            )

        lines.extend(["", "## Highest-friction sessions"])
        friction_sessions = sorted(
            self.sessions,
            key=lambda session: (
                -session.friction_score(),
                -session.tool_failures,
                session.created_at,
            ),
        )
        added_friction = 0
        for session in friction_sessions:
            if session.friction_score() <= 0:
                continue
            lines.append(session.friction_line())
            added_friction += 1
            if added_friction >= FRICTION_LIMIT:
                break
        if added_friction == 0:
            lines.append(
                "- No strong friction signal stood out in the sampled sessions."
            )

        lines.extend(["", "## Session summaries"])
        lines.extend(session.summary_line() for session in self.sessions)
        return "\n".join(lines)


def build_improve_prompt(
    session_config: SessionLoggingConfig,
    current_session_dir: Path | None = None,
    limit: int = DEFAULT_IMPROVE_SESSION_LIMIT,
) -> str:
    """
    Build an improvement prompt by analyzing recent Vibe sessions.
    
    This function analyzes recent interactive sessions to generate a comprehensive
    prompt that can be used to get suggestions for improving Vibe's functionality,
    defaults, and user experience.
    
    Args:
        session_config: Configuration for session logging
        current_session_dir: Optional path to the current session directory to exclude
        limit: Maximum number of recent sessions to analyze
        
    Returns:
        A formatted prompt string containing session analysis and improvement suggestions
        
    Raises:
        ValueError: If no previous sessions are found or cannot be read
        
    Note:
        The function uses lazy loading and early termination to efficiently handle
        large numbers of sessions. It stops processing once the requested limit
        of valid sessions is reached.
    """
    requested_limit = max(limit, 1)
    
    # Use generator for lazy loading of session directories
    session_dirs = collect_recent_session_dirs(
        session_config=session_config,
        current_session_dir=current_session_dir,
    )
    
    if not session_dirs:
        raise ValueError("No previous interactive sessions were found to analyze.")

    analyses: list[SessionAnalysis] = []
    
    # Process sessions lazily - stop when we have enough valid analyses
    for session_dir in session_dirs:
        if len(analyses) >= requested_limit:
            break
            
        try:
            messages, metadata = SessionLoader.load_session(session_dir)
        except ValueError:
            # Skip corrupted or invalid sessions
            continue
            
        try:
            analysis = SessionAnalysis.from_session(session_dir, messages, metadata)
        except Exception:
            # Skip sessions that fail analysis
            continue
            
        if not analysis.has_signal():
            continue
            
        analyses.append(analysis)

    if not analyses:
        raise ValueError("Unable to read any previous interactive sessions.")

    digest = ImproveDigest.from_sessions(analyses)
    return digest.render_prompt(requested_limit=requested_limit)


def collect_recent_session_dirs(
    session_config: SessionLoggingConfig,
    current_session_dir: Path | None = None,
) -> list[Path]:
    """
    Collect recent session directories for analysis.
    
    This function finds all session directories matching the configured prefix,
    excluding the current session if provided, and returns them sorted by
    modification time (newest first).
    
    Args:
        session_config: Configuration containing session directory location and prefix
        current_session_dir: Optional path to exclude (typically the current session)
        
    Returns:
        List of Path objects to session directories, sorted from newest to oldest
        
    Note:
        This function uses a generator expression for memory efficiency when
        dealing with large numbers of session directories.
    """
    save_dir = Path(session_config.save_dir)
    if not save_dir.exists():
        return []

    pattern = f"{session_config.session_prefix}_*"
    return sorted(
        (
            session_dir
            for session_dir in save_dir.glob(pattern)
            if session_dir.is_dir() and session_dir != current_session_dir
        ),
        key=session_sort_key,
        reverse=True,
    )


def session_sort_key(session_dir: Path) -> tuple[float, str]:
    """
    Generate a sort key for session directories.
    
    This function creates a tuple key for sorting session directories
    by modification time (primary) and name (secondary).
    
    Args:
        session_dir: Path to the session directory
        
    Returns:
        Tuple of (modification_time, directory_name) for sorting
        
    Note:
        Uses 0.0 as fallback modification time if the messages file doesn't exist
        or cannot be accessed.
    """
    messages_path = session_dir / MESSAGES_FILENAME
    try:
        mtime = messages_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (mtime, session_dir.name)


def extract_message_text(message: LLMMessage) -> str | None:
    """
    Extract and normalize text content from an LLM message.
    
    Args:
        message: The LLMMessage to extract text from
        
    Returns:
        Normalized text content or None if message has no content
    """
    if message.content is None:
        return None
    text = collapse_whitespace(message.content)
    return text or None


def tool_message_tag(content: str) -> str:
    """
    Extract the tag from a tool message content.
    
    Args:
        content: The tool message content
        
    Returns:
        The extracted tag from the content
    """
    return TaggedText.from_string(content).tag


def summarize_bash_command(raw_arguments: str | None) -> str | None:
    """
    Summarize a bash command from tool call arguments.
    
    This function extracts and summarizes bash commands from tool call arguments,
    handling both JSON-formatted arguments and raw strings.
    
    Args:
        raw_arguments: Raw arguments string from a tool call
        
    Returns:
        Short summary of the bash command or None if extraction fails
        
    Note:
        Returns the first 4 words of the first shell segment for brevity.
    """
    if not raw_arguments:
        return None

    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        collapsed = collapse_whitespace(raw_arguments)
        return shorten(collapsed, PREVIEW_CHAR_LIMIT) if collapsed else None

    if not isinstance(parsed, dict):
        return None
    command = parsed.get("command")
    if not isinstance(command, str):
        return None

    segment = first_shell_segment(command)
    summary = take_command_words(segment, 4)
    if summary:
        return summary
    collapsed = collapse_whitespace(command)
    return shorten(collapsed, PREVIEW_CHAR_LIMIT) if collapsed else None


def assistant_phrase_sample(text: str) -> str | None:
    """
    Extract a sample phrase from assistant message text.
    
    This function extracts the first non-empty, non-code line from assistant text
    and shortens it for use in repetition detection.
    
    Args:
        text: The assistant message text
        
    Returns:
        Shortened sample phrase or None if no suitable text found
    """
    first_line = next(
        (
            stripped
            for line in text.splitlines()
            if (stripped := line.strip()) and not stripped.startswith("```")
        ),
        None,
    )
    if first_line is None:
        return None

    shortened = shorten(first_line, PHRASE_CHAR_LIMIT)
    return shortened if repetition_key(shortened) is not None else None


def is_improve_generated_prompt(text: str) -> bool:
    """
    Check if text is an auto-generated improve prompt.
    
    This function detects whether a given text is an automatically generated
    improve prompt that should be excluded from analysis.
    
    Args:
        text: The text to check
        
    Returns:
        True if the text matches the pattern of an auto-generated improve prompt
    """
    normalized = collapse_whitespace(text)
    return (
        normalized.startswith("Analyze this digest of my last ")
        and "interactive Mistral Vibe sessions and tell me how Vibe should improve."
        in normalized
        and "Respond with these sections:" in normalized
        and "1. Friction to address" in normalized
        and "4. Ship next" in normalized
    )


def repetition_key(text: str) -> str | None:
    """
    Generate a normalized key for detecting repeated phrases.
    
    This function normalizes text for repetition detection by:
    - Converting to lowercase
    - Removing non-alphanumeric characters
    - Expanding common contractions (ll->will, ve->have, etc.)
    - Requiring minimum length and word count
    
    Args:
        text: The text to generate a key for
        
    Returns:
        Normalized key string or None if text is too short
    """
    normalized_chars: list[str] = []
    previous_space = False
    for char in text:
        if char.isascii() and char.isalnum():
            normalized_chars.append(char.lower())
            previous_space = False
            continue
        if previous_space or not normalized_chars:
            continue
        normalized_chars.append(" ")
        previous_space = True

    normalized_words: list[str] = []
    for word in "".join(normalized_chars).split():
        if word == "ll" and normalized_words:
            normalized_words.append("will")
            continue
        if word == "ve" and normalized_words:
            normalized_words.append("have")
            continue
        if word == "re" and normalized_words:
            normalized_words.append("are")
            continue
        if word == "m" and normalized_words:
            normalized_words.append("am")
            continue
        normalized_words.append(word)

    normalized = " ".join(normalized_words)
    if len(normalized) < 24 or len(normalized_words) < 4:
        return None
    return normalized


def first_shell_segment(command: str) -> str:
    """
    Extract the first shell segment from a command string.
    
    This function finds the first segment of a shell command by looking for
    common shell separators (&&, ||, ;, |, newline).
    
    Args:
        command: The full command string
        
    Returns:
        The first segment of the command before any shell separators
    """
    best = len(command)
    for needle in ("&&", "||", ";", "|", "\n"):
        index = command.find(needle)
        if index == -1:
            continue
        best = min(best, index)
    return command[:best].strip()


def take_command_words(text: str, limit: int) -> str:
    """
    Take the first N words from text.
    
    Args:
        text: The input text
        limit: Maximum number of words to return
        
    Returns:
        String containing the first N words
    """
    return " ".join(text.split()[:limit])


def collapse_whitespace(text: str) -> str:
    """
    Collapse multiple whitespace characters into single spaces.
    
    Args:
        text: Input text with potential multiple/mixed whitespace
        
    Returns:
        Text with whitespace normalized to single spaces
    """
    return " ".join(text.split())


def shorten(text: str, max_chars: int) -> str:
    """
    Shorten text to a maximum character limit with ellipsis.
    
    Args:
        text: Input text to shorten
        max_chars: Maximum number of characters to return
        
    Returns:
        Shortened text with ellipsis if truncated, original text if within limit
    """
    collapsed = collapse_whitespace(text)
    if len(collapsed) <= max_chars:
        return collapsed
    return f"{collapsed[:max_chars]}..."


def shorten_path(path: Path) -> str:
    """
    Shorten a path string for display.
    
    Args:
        path: Path object to shorten
        
    Returns:
        Shortened path string (max 72 characters)
    """
    return shorten(str(path), 72)


def top_counts(counts: Counter[str], limit: int) -> list[tuple[str, int]]:
    """
    Get the top N items from a counter by count.
    
    Args:
        counts: Counter object with item counts
        limit: Maximum number of top items to return
        
    Returns:
        List of (item, count) tuples sorted by count descending, then by item name
    """
    return sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))[:limit]


def format_count_entries(entries: list[tuple[str, int]]) -> str:
    """
    Format count entries as a comma-separated string.
    
    Args:
        entries: List of (name, count) tuples
        
    Returns:
        Formatted string like "item1 x3, item2 x2"
    """
    return ", ".join(f"{name} x{count}" for name, count in entries)


def render_count_section(entries: list[tuple[str, int]]) -> list[str]:
    """
    Render count entries as a list of formatted strings.
    
    Args:
        entries: List of (name, count) tuples
        
    Returns:
        List of formatted strings like ["- item1: 3", "- item2: 2"]
    """
    if not entries:
        return ["- None recorded in the sampled sessions."]
    return [f"- {name}: {count}" for name, count in entries]
