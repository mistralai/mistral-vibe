"""PowerShell lexing for the shell permission guardrail.

Follows the command-mode rules of PowerShell's own tokenizer
(System.Management.Automation/engine/parser/tokenizer.cs). Wherever this lexer
and PowerShell could still disagree, it errs towards seeing more commands,
arguments and redirections than PowerShell runs, never fewer.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, NamedTuple

_SINGLE_QUOTES = frozenset("'‘’‚‛")
_DOUBLE_QUOTES = frozenset('"“”„')
_QUOTES = _SINGLE_QUOTES | _DOUBLE_QUOTES
_DASHES = frozenset("-–—―")
_NEWLINES = frozenset("\r\n")
# str.isspace() also counts these, but PowerShell keeps them inside a word.
_WORD_CONTROL_CHARACTERS = frozenset("\x1c\x1d\x1e\x1f")
# PowerShell only ends an argument at "&(),;{|}", but a mid-word ">" is treated
# as a redirection too so that no redirection target can be missed.
_ARGUMENT_BREAKS = frozenset("&(),;{|}>")
_PARAMETER_BREAKS = _ARGUMENT_BREAKS | frozenset(".[")
_BACKTICK_ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}
_UNICODE_ESCAPE = re.compile(r"\{([0-9A-Fa-f]{1,6})\}")
_MAX_CODE_POINT = 0x10FFFF
_STOP_PARSING = "--%"


@dataclass(frozen=True, slots=True)
class _Token:
    kind: Literal["argument", "redirect", "separator"]
    start: int
    end: int
    value: str | None = None
    spelling: str | None = None
    """A redirect's target as written, before quotes and escapes are read."""
    array: bool = False
    """Whether a redirect's target goes on as an array (``> a, b``)."""


def _is_space(char: str) -> bool:
    return char == "\0" or (char.isspace() and char not in _WORD_CONTROL_CHARACTERS)


class _Lexer:
    def __init__(self, text: str, start: int = 0) -> None:
        self.text = text
        self.index = start
        self.tokens: list[_Token] = []
        self.nested: list[str] = []
        self.colon_parameter_end = -1
        self.after_comma = False

    def peek(self, offset: int = 0) -> str:
        index = self.index + offset
        return self.text[index] if index < len(self.text) else ""

    def lex(self, closer: str | None = None) -> None:
        while self.index < len(self.text):
            char = self.text[self.index]
            if char == closer:
                return
            if char in _NEWLINES or char in ";|&":
                self._separator()
            elif char == ",":
                self.after_comma = True
                self.index += 1
            elif _is_space(char) or char in ")}":
                self.index += 1
            elif char == "`" and (not self.peek(1) or _is_space(self.peek(1))):
                self._line_continuation()
            elif char == "#":
                self._skip_line_comment()
            elif char == "<" and self.peek(1) == "#":
                self._skip_block_comment()
            elif char == ">" or (char in "123456*" and self.peek(1) == ">"):
                self._redirect()
            else:
                self._argument()

    def _separator(self) -> None:
        start = self.index
        pair = self.text[start : start + 2]
        self.index += 2 if pair in {"\r\n", "&&", "||"} else 1
        self.tokens.append(_Token("separator", start, self.index))

    def _line_continuation(self) -> None:
        self.index += 1
        if self.text.startswith("\r\n", self.index):
            self.index += 1
        self.index += 1

    def _skip_line_comment(self) -> None:
        while self.index < len(self.text) and self.text[self.index] not in _NEWLINES:
            self.index += 1

    def _skip_block_comment(self) -> None:
        end = self.text.find("#>", self.index + 2)
        self.index = len(self.text) if end == -1 else end + 2

    def _group(self) -> str:
        """Skip a bracketed group, recording its body as a nested script."""
        opening = self.index
        closer = ")" if self.text[opening] == "(" else "}"
        body = _Lexer(self.text, opening + 1)
        body.lex(closer)
        self.nested.append(self.text[opening + 1 : body.index])
        self.index = min(body.index + 1, len(self.text))
        return self.text[opening : self.index]

    def _redirect(self) -> None:
        start = self.index
        if self.text[self.index] != ">":
            self.index += 1
        self.index += 1
        if self.peek() == ">":
            self.index += 1
        if self.peek() == "&":
            self.index += 1
            if self.peek().isdigit():
                self.index += 1
            self.tokens.append(_Token("redirect", start, self.index))
            return
        while _is_space(self.peek()) and self.peek() not in _NEWLINES:
            self.index += 1
        target_start = self.index
        target = self._word() if self.peek() else ""
        # The array's later elements are still lexed as arguments, as before.
        array_end = self._array_end(self.index)
        self.tokens.append(
            _Token(
                "redirect",
                start,
                self.index,
                target or None,
                self.text[target_start:array_end],
                array=array_end > self.index,
            )
        )

    def _array_end(self, index: int) -> int:
        """Where a word continued by ``,`` ends: PowerShell reads ``a, b`` as one array."""
        probe = _Lexer(self.text, index)
        end = index
        while True:
            while _is_space(probe.peek()) and probe.peek() not in _NEWLINES:
                probe.index += 1
            if probe.peek() != ",":
                return end
            probe.index += 1
            while _is_space(probe.peek()):
                probe.index += 1
            if probe.peek():
                probe._word()
            end = probe.index

    def _argument(self) -> None:
        # PowerShell binds the word after "-name:" to that parameter, and reads
        # the word after "," (even on the next line) as one more array element,
        # even when that word is "--%".
        extends_previous_word = self.after_comma or (
            bool(self.tokens) and self.tokens[-1].end == self.colon_parameter_end
        )
        self.after_comma = False
        start = self.index
        value = self._word()
        self.tokens.append(_Token("argument", start, self.index, value))
        # Stop-parsing is decided on the decoded word, so '--%' and -`-% count.
        # Windows PowerShell 5.1 has no `u{} escape, so a word that needs one to
        # spell --% is read normally there, and here too.
        if (
            value == _STOP_PARSING
            and not extends_previous_word
            and "`u" not in self.text[start : self.index]
        ):
            self._verbatim_arguments()

    def _word(self) -> str:  # noqa: PLR0911 - one branch per kind of word
        char, next_char = self.peek(), self.peek(1)
        if char in _SINGLE_QUOTES:
            return self._single_quoted()
        if char in _DOUBLE_QUOTES:
            return self._double_quoted()
        if char == "@" and next_char in _QUOTES:
            here_string = self._here_string()
            if here_string is not None:
                return here_string
        if (char == "$" and next_char == "(") or (
            char == "@" and next_char in {"(", "{"}
        ):
            self.index += 1
            return char + self._group() + self._generic()
        if char in {"(", "{"}:
            return self._group()
        if char in _DASHES and (next_char.isalpha() or next_char in {"_", "?"}):
            return self._parameter()
        return self._generic()

    def _generic(self) -> str:
        value: list[str] = []
        while self.index < len(self.text):
            char = self.text[self.index]
            if _is_space(char) or char in _ARGUMENT_BREAKS:
                break
            if char == "`":
                value.append(self._backtick())
            elif char in _SINGLE_QUOTES:
                value.append(self._single_quoted())
            elif char in _DOUBLE_QUOTES:
                value.append(self._double_quoted())
            elif char == "$" and self.peek(1) in {"(", "{"}:
                value.append(self._variable_or_subexpression())
            else:
                value.append(char)
                self.index += 1
        return "".join(value)

    def _parameter(self) -> str:
        start = self.index
        self.index += 1
        while self.index < len(self.text):
            char = self.text[self.index]
            if char in _QUOTES:
                # PowerShell rescans a parameter holding a quote as an argument.
                self.index = start
                return self._generic()
            if _is_space(char) or char in _PARAMETER_BREAKS:
                break
            self.index += 1
            if char == ":":
                self.colon_parameter_end = self.index
                break
        return self.text[start : self.index]

    def _backtick(self) -> str:
        escaped = self.peek(1)
        if not escaped:
            self.index += 1
            return "`"
        self.index += 2
        if escaped == "u" and (match := _UNICODE_ESCAPE.match(self.text, self.index)):
            code_point = int(match[1], 16)
            if code_point <= _MAX_CODE_POINT:
                self.index = match.end()
                return chr(code_point)
        return _BACKTICK_ESCAPES.get(escaped, escaped)

    def _single_quoted(self) -> str:
        value: list[str] = []
        self.index += 1
        while self.index < len(self.text):
            char = self.text[self.index]
            self.index += 1
            if char not in _SINGLE_QUOTES:
                value.append(char)
            elif self.peek() and self.peek() in _SINGLE_QUOTES:
                value.append(self.peek())
                self.index += 1
            else:
                break
        return "".join(value)

    def _double_quoted(self) -> str:
        value: list[str] = []
        self.index += 1
        while self.index < len(self.text):
            char = self.text[self.index]
            if char in _DOUBLE_QUOTES:
                self.index += 1
                if not (self.peek() and self.peek() in _DOUBLE_QUOTES):
                    break
                value.append(self.peek())
                self.index += 1
            elif char == "`":
                value.append(self._backtick())
            elif char == "$" and self.peek(1) in {"(", "{"}:
                value.append(self._variable_or_subexpression())
            else:
                value.append(char)
                self.index += 1
        return "".join(value)

    def _here_string(self) -> str | None:
        quotes = _SINGLE_QUOTES if self.peek(1) in _SINGLE_QUOTES else _DOUBLE_QUOTES
        index = self.index + 2
        while index < len(self.text) and _is_space(self.text[index]):
            if self.text[index] in _NEWLINES:
                break
            index += 1
        if index >= len(self.text) or self.text[index] not in _NEWLINES:
            return None
        index += 2 if self.text.startswith("\r\n", index) else 1

        body_start = footer = index
        while footer < len(self.text) and not (
            self.text[footer] in quotes and self.text[footer + 1 : footer + 2] == "@"
        ):
            footer = self._next_line(footer)
        body = self.text[body_start:footer].removesuffix("\n").removesuffix("\r")
        self.index = min(footer + 2, len(self.text))
        if quotes is _DOUBLE_QUOTES:
            self._record_subexpressions(body)
        return body

    def _next_line(self, index: int) -> int:
        while index < len(self.text) and self.text[index] not in _NEWLINES:
            index += 1
        if self.text.startswith("\r\n", index):
            return index + 2
        return index + 1

    def _variable_or_subexpression(self) -> str:
        start = self.index
        if self.peek(1) == "{":
            end = self.text.find("}", start + 2)
            self.index = len(self.text) if end == -1 else end + 1
            return self.text[start : self.index]
        # PowerShell tokenizes "$(" like any script, even inside a string, so a
        # ")" in a quote or comment does not close it.
        self.index += 1
        return "$" + self._group()

    def _record_subexpressions(self, body: str) -> None:
        scanner = _Lexer(body)
        index = body.find("$(")
        while index != -1:
            scanner.index = index + 1
            scanner._group()
            index = body.find("$(", scanner.index)
        self.nested.extend(scanner.nested)

    def _verbatim_arguments(self) -> None:
        # After --%, PowerShell hands the rest of the line to the native
        # command line verbatim, stopping only at "|" or "&&" outside quotes.
        in_quotes = False
        start: int | None = None
        value: list[str] = []
        while self.index < len(self.text):
            char = self.text[self.index]
            if char in _NEWLINES:
                break
            if not in_quotes and (
                char == "|" or self.text.startswith("&&", self.index)
            ):
                break
            if _is_space(char) and not in_quotes:
                if start is not None:
                    self.tokens.append(
                        _Token("argument", start, self.index, "".join(value))
                    )
                start = None
                value.clear()
            else:
                if start is None:
                    start = self.index
                if char in _DOUBLE_QUOTES:
                    in_quotes = not in_quotes
                else:
                    value.append(char)
            self.index += 1
        if start is not None:
            self.tokens.append(_Token("argument", start, self.index, "".join(value)))


def _lex(script: str) -> _Lexer:
    lexer = _Lexer(script)
    lexer.lex()
    return lexer


def split_powershell_commands(script: str) -> list[str]:
    """Split a script into its statements, then those nested in groups."""
    lexer = _lex(script)
    commands: list[str] = []
    span: tuple[int, int] | None = None
    for token in [*lexer.tokens, _Token("separator", len(script), len(script))]:
        if token.kind != "separator":
            span = (token.start if span is None else span[0], token.end)
            continue
        if span is not None:
            commands.append(script[span[0] : span[1]].strip())
        span = None
    for nested in lexer.nested:
        commands.extend(split_powershell_commands(nested))
    return commands


def split_powershell_arguments(command: str) -> list[str]:
    return [
        token.value
        for token in _lex(command).tokens
        if token.kind == "argument" and token.value is not None
    ]


def powershell_redirection_targets(command: str) -> list[str]:
    return [
        token.value
        for token in _lex(command).tokens
        if token.kind == "redirect" and token.value is not None
    ]


class PowerShellRedirect(NamedTuple):
    spelling: str
    """The target as written; an array's every element."""
    value: str
    """The target's first word as PowerShell reads it."""
    array: bool
    """Whether the target goes on as an array (``> a, b``)."""


def powershell_redirections(script: str) -> list[PowerShellRedirect]:
    """Each redirect's target, in nested scripts too.

    A script block or subexpression may run, so its redirects count.
    """
    lexer = _lex(script)
    found = [
        PowerShellRedirect(token.spelling, token.value, token.array)
        for token in lexer.tokens
        if token.kind == "redirect"
        and token.value is not None
        and token.spelling is not None
    ]
    for nested in lexer.nested:
        found.extend(powershell_redirections(nested))
    return found
