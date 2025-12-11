from __future__ import annotations

from pathlib import Path

import pytest
from textual import events

from vibe.cli.autocompletion.base import CompletionResult, CompletionView
from vibe.cli.autocompletion.path_completion import PathCompletionController
from vibe.core.autocompletion.completers import PathCompleter


class StubView(CompletionView):
    def __init__(self) -> None:
        self.suggestions: list[tuple[list[tuple[str, str]], int]] = []
        self.clears = 0
        self.replacements: list[tuple[int, int, str]] = []

    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        self.suggestions.append((suggestions, selected_index))

    def clear_completion_suggestions(self) -> None:
        self.clears += 1

    def replace_completion_range(self, start: int, end: int, replacement: str) -> None:
        self.replacements.append((start, end, replacement))


@pytest.fixture()
def file_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "core" / "logger.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "models.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "ports.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "sanitize.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "use_cases.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "validate.py").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_controller(
    max_entries_to_process: int | None = None, target_matches: int | None = None
) -> tuple[PathCompletionController, StubView]:
    completer_kwargs = {}
    if max_entries_to_process is not None:
        completer_kwargs["max_entries_to_process"] = max_entries_to_process
    if target_matches is not None:
        completer_kwargs["target_matches"] = target_matches

    completer = PathCompleter(**completer_kwargs)
    view = StubView()
    controller = PathCompletionController(completer, view)
    return controller, view


def test_lists_root_entries(file_tree: Path) -> None:
    controller, view = make_controller()

    controller.on_text_changed("@", cursor_index=1)

    suggestions, selected = view.suggestions[-1]
    assert selected == 0
    assert [alias for alias, _ in suggestions] == ["@README.md", "@src/"]


def test_suggests_hidden_entries_only_with_dot_prefix(file_tree: Path) -> None:
    controller, view = make_controller()

    controller.on_text_changed("@.", cursor_index=2)

    suggestions, _ = view.suggestions[-1]
    assert suggestions[0][0] == "@.env"


def test_lists_nested_entries_when_prefixing_with_folder_name(file_tree: Path) -> None:
    controller, view = make_controller()

    controller.on_text_changed("@src/", cursor_index=5)

    suggestions, _ = view.suggestions[-1]
    assert [alias for alias, _ in suggestions] == [
        "@src/core/",
        "@src/main.py",
        "@src/utils/",
    ]


def test_resets_when_fragment_invalid(file_tree: Path) -> None:
    controller, view = make_controller()

    controller.on_text_changed("@src", cursor_index=4)
    assert view.suggestions

    controller.on_text_changed("@src foo", cursor_index=8)
    assert view.clears == 1
    assert (
        controller.on_key(events.Key("tab", None), "@src foo", 8)
        is CompletionResult.IGNORED
    )


def test_applies_selected_completion_on_tab_keycode(file_tree: Path) -> None:
    controller, view = make_controller()

    controller.on_text_changed("@R", cursor_index=2)
    result = controller.on_key(events.Key("tab", None), "@R", 2)

    assert result is CompletionResult.HANDLED
    assert view.replacements == [(0, 2, "@README.md")]
    assert view.clears == 1


def test_applies_selected_completion_on_enter_keycode(file_tree: Path) -> None:
    controller, view = make_controller()
    controller.on_text_changed("@src/", cursor_index=5)
    controller.on_key(events.Key("down", None), "@src/", 5)

    result = controller.on_key(events.Key("enter", None), "@src/", 5)

    assert result is CompletionResult.HANDLED
    assert view.replacements == [(0, 5, "@src/main.py")]
    assert view.clears == 1


def test_navigates_and_cycles_across_suggestions(file_tree: Path) -> None:
    controller, view = make_controller()

    controller.on_text_changed("@src/", cursor_index=5)
    controller.on_key(events.Key("down", None), "@src/", 5)
    suggestions, selected_index = view.suggestions[-1]
    assert [alias for alias, _ in suggestions] == [
        "@src/core/",
        "@src/main.py",
        "@src/utils/",
    ]
    assert selected_index == 1
    controller.on_key(events.Key("up", None), "@src/", 5)
    suggestions, selected_index = view.suggestions[-1]
    assert selected_index == 0

    controller.on_key(events.Key("down", None), "@src/", 5)
    controller.on_key(events.Key("down", None), "@src/", 5)
    suggestions, selected_index = view.suggestions[-1]
    assert selected_index == 2

    controller.on_key(events.Key("down", None), "@src/", 5)
    suggestions, selected_index = view.suggestions[-1]
    assert selected_index == 0


def test_limits_suggestions_to_ten(file_tree: Path) -> None:
    (file_tree / "src" / "core" / "extra").mkdir(parents=True)
    [
        (file_tree / "src" / "core" / "extra" / f"extra_file_{i}.py").write_text(
            "", encoding="utf-8"
        )
        for i in range(1, 13)
    ]
    controller, view = make_controller()

    controller.on_text_changed("@src/core/extra/", cursor_index=16)
    suggestions, selected_index = view.suggestions[-1]
    assert len(suggestions) == 10
    assert [alias for alias, _ in suggestions] == [
        "@src/core/extra/extra_file_1.py",
        "@src/core/extra/extra_file_10.py",
        "@src/core/extra/extra_file_11.py",
        "@src/core/extra/extra_file_12.py",
        "@src/core/extra/extra_file_2.py",
        "@src/core/extra/extra_file_3.py",
        "@src/core/extra/extra_file_4.py",
        "@src/core/extra/extra_file_5.py",
        "@src/core/extra/extra_file_6.py",
        "@src/core/extra/extra_file_7.py",
    ]
    assert selected_index == 0


def test_does_not_handle_when_cursor_at_beginning_of_input(file_tree: Path) -> None:
    controller, _ = make_controller()

    assert not controller.can_handle("@file", cursor_index=0)
    assert not controller.can_handle("", cursor_index=0)
    assert not controller.can_handle("hello@file", cursor_index=0)


def test_does_not_handle_when_cursor_before_or_at_the_at_symbol(
    file_tree: Path,
) -> None:
    controller, _ = make_controller()

    assert not controller.can_handle("@file", cursor_index=0)
    assert not controller.can_handle("hello@file", cursor_index=5)


def test_does_handle_when_cursor_after_the_at_symbol_even_in_the_middle_of_the_input(
    file_tree: Path,
) -> None:
    controller, _ = make_controller()

    assert controller.can_handle("@file", cursor_index=1)
    assert controller.can_handle("hello @file", cursor_index=7)


def test_lists_immediate_children_when_path_ends_with_slash(file_tree: Path) -> None:
    controller, view = make_controller()

    controller.on_text_changed("@src/", cursor_index=5)

    suggestions, _ = view.suggestions[-1]
    assert [alias for alias, _ in suggestions] == [
        "@src/core/",
        "@src/main.py",
        "@src/utils/",
    ]


def test_respects_max_entries_to_process_limit(file_tree: Path) -> None:
    for i in range(30):
        (file_tree / f"file_{i:03d}.txt").write_text("", encoding="utf-8")

    controller, view = make_controller(max_entries_to_process=10)

    controller.on_text_changed("@", cursor_index=1)

    suggestions, _ = view.suggestions[-1]
    assert len(suggestions) <= 10


def test_respects_target_matches_limit_for_listing(file_tree: Path) -> None:
    for i in range(30):
        (file_tree / f"item_{i:03d}.txt").write_text("", encoding="utf-8")

    controller, view = make_controller(target_matches=5)

    controller.on_text_changed("@", cursor_index=1)

    suggestions, _ = view.suggestions[-1]
    assert len(suggestions) <= 5


def test_respects_target_matches_limit_for_fuzzy_search(file_tree: Path) -> None:
    for i in range(30):
        (file_tree / f"test_file_{i:03d}.py").write_text("", encoding="utf-8")

    controller, view = make_controller(target_matches=5)

    controller.on_text_changed("@test", cursor_index=5)

    suggestions, _ = view.suggestions[-1]
    assert len(suggestions) <= 5
