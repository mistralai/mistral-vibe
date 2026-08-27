from __future__ import annotations

from collections.abc import Callable

from textual import events

from vibe.cli.autocompletion.base import CompletionResult, InlineSuggestionView

# A mid-prompt skill token is at minimum a slash plus one character.
_MIN_SKILL_TOKEN_LEN = 2


class InlineSkillCompletionController:
    """Ghost-text completion for skills typed mid-prompt.

    When the word ending at the cursor starts with `/` and is not the first
    word of the input, the best-matching skill is previewed inline (via the
    TextArea `suggestion` reactive) and accepted with Tab. The start-of-line
    slash popup is left to `SlashCommandController`.
    """

    def __init__(
        self,
        skill_entries: Callable[[], list[tuple[str, str]]],
        view: InlineSuggestionView,
        is_default_mode: Callable[[], bool],
    ) -> None:
        self._get_skill_entries = skill_entries
        self._view = view
        self._is_default_mode = is_default_mode
        self._match: str | None = None
        self._token_start = 0
        self._token_end = 0

    def can_handle(self, text: str, cursor_index: int) -> bool:
        return self._token_span(text, cursor_index) is not None

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        span = self._token_span(text, cursor_index)
        if span is None:
            self.reset()
            return

        token_start, token_end = span
        match = self._best_match(text[token_start:token_end])
        if match is None:
            self.reset()
            return

        self._match = match
        self._token_start = token_start
        self._token_end = token_end
        self._view.show_inline_suggestion(match[token_end - token_start :])

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        # Only Tab is handled here. Caret moves that retire the ghost (arrows,
        # word/line nav, clicks) are re-evaluated by the text area via
        # on_text_changed, so this controller never dismisses on navigation keys.
        if event.key == "tab":
            return self._accept(text, cursor_index)
        return CompletionResult.IGNORED

    def is_showing(self) -> bool:
        return self._match is not None

    def reset(self) -> None:
        if self._match is None:
            return
        self._match = None
        self._token_start = 0
        self._token_end = 0
        self._view.clear_inline_suggestion()

    def _accept(self, text: str, cursor_index: int) -> CompletionResult:
        if self._match is None:
            return CompletionResult.IGNORED
        # A cursor move that never routed through the manager (e.g. a mouse
        # click) can leave a stale span/match. Only accept when the caret is
        # still at the end of the same token and the ghost still applies.
        if self._token_span(text, cursor_index) != (self._token_start, self._token_end):
            self.reset()
            return CompletionResult.IGNORED
        token = text[self._token_start : self._token_end]
        if not self._match.lower().startswith(token.lower()):
            self.reset()
            return CompletionResult.IGNORED
        # Do not suppress the follow-up recompute: after accepting, a deeper
        # skill (e.g. /implement -> /implement-plan) should surface its ghost,
        # matching right-arrow acceptance via Textual's native suggestion.
        self._view.replace_completion_range(
            self._token_start, self._token_end, self._match
        )
        self.reset()
        return CompletionResult.HANDLED

    def _best_match(self, token: str) -> str | None:
        needle = token.lower()
        candidates = sorted(
            name
            for name, _ in self._get_skill_entries()
            if len(name) > len(token) and name.lower().startswith(needle)
        )
        return candidates[0] if candidates else None

    def _token_span(self, text: str, cursor_index: int) -> tuple[int, int] | None:
        if cursor_index <= 0 or cursor_index > len(text):
            return None
        # Only the default prompt mode carries mid-prompt skills. Detect the mode
        # from the input state, not from a leading char — in default mode a
        # pasted/edited line can legitimately start with `/`, `!`, or `&`.
        if not self._is_default_mode():
            return None

        following = text[cursor_index : cursor_index + 1]
        if following and not following.isspace():
            return None

        token_start = self._word_start(text, cursor_index)
        # A token preceded only by whitespace is still the first word, which
        # belongs to the slash popup — not the mid-prompt ghost.
        if not text[:token_start].strip():
            return None

        token = text[token_start:cursor_index]
        if not self._is_mid_prompt_skill_token(token):
            return None

        return token_start, cursor_index

    @staticmethod
    def _is_mid_prompt_skill_token(token: str) -> bool:
        if len(token) < _MIN_SKILL_TOKEN_LEN or not token.startswith("/"):
            return False
        return "/" not in token[1:] and "@" not in token

    @staticmethod
    def _word_start(text: str, cursor_index: int) -> int:
        start = cursor_index
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        return start
