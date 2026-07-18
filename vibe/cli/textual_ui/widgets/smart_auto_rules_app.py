from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

if TYPE_CHECKING:
    from vibe.core.config import AnyVibeConfig


CURATED_ASK_RULES: tuple[str, ...] = (
    "Changing credentials, roles, permissions, or access-control policies.",
    "Publishing a release, package, or artifact for external users.",
)


class SmartAutoRulesApp(Container):
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close_or_cancel", "Close", show=False),
        Binding("x", "remove_rule", "Remove rule", show=False),
    ]

    class RulesClosed(Message):
        def __init__(self, ask_rules: list[str], allow_rules: list[str]) -> None:
            super().__init__()
            self.ask_rules = ask_rules
            self.allow_rules = allow_rules

    def __init__(self, config: AnyVibeConfig) -> None:
        super().__init__(id="smartautorules-app")
        self.ask_rules = list(config.auto_mode.soft_deny)
        self.allow_rules = list(config.auto_mode.allow)
        self._adding: Literal["ask", "allow"] | None = None

    @property
    def _custom_ask_rules(self) -> list[str]:
        return [rule for rule in self.ask_rules if rule not in CURATED_ASK_RULES]

    def _rule_options(self) -> list[Option]:
        options = [
            Option(
                f"[{'✓' if rule in self.ask_rules else ' '}] ASK  {rule}",
                id=f"curated:{index}",
            )
            for index, rule in enumerate(CURATED_ASK_RULES)
        ]
        options.extend(
            Option(f"ASK    {rule}", id=f"ask:{index}")
            for index, rule in enumerate(self._custom_ask_rules)
        )
        options.extend(
            Option(f"ALLOW  {rule}", id=f"allow:{index}")
            for index, rule in enumerate(self.allow_rules)
        )
        options.extend([
            Option("+ Add an ASK rule", id="action:add-ask"),
            Option("+ Add an ALLOW rule", id="action:add-allow"),
            Option("Save and close", id="action:save"),
        ])
        return options

    def compose(self) -> ComposeResult:
        with Vertical(id="smartautorules-content"):
            yield NoMarkupStatic("Careful YOLO rules", classes="smartautorules-title")
            yield NoMarkupStatic(
                "ASK rules request approval. ALLOW rules describe routine actions. "
                "ASK always wins when rules overlap.",
                classes="smartautorules-description",
            )
            yield NavigableOptionList(
                *self._rule_options(), id="smartautorules-options"
            )
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Select  "
                    f"{shortcut('X')} Remove custom rule  {shortcut('Esc')} Save & close"
                ),
                classes="smartautorules-help",
            )

        with Vertical(id="smartautorules-editor"):
            yield NoMarkupStatic("", id="smartautorules-editor-title")
            yield Input(id="smartautorules-input")
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('Enter')} Add rule  {shortcut('Esc')} Cancel"
                ),
                classes="smartautorules-help",
            )

    def on_mount(self) -> None:
        self.query_one("#smartautorules-editor").display = False
        self.query_one(OptionList).focus()

    def _refresh_options(self, *, highlighted: int | None = None) -> None:
        option_list = self.query_one(OptionList)
        current = option_list.highlighted if highlighted is None else highlighted
        option_list.clear_options()
        option_list.add_options(self._rule_options())
        if current is not None and option_list.option_count:
            option_list.highlighted = min(current, option_list.option_count - 1)

    def _start_add(self, kind: Literal["ask", "allow"]) -> None:
        self._adding = kind
        self.query_one("#smartautorules-content").display = False
        self.query_one("#smartautorules-editor").display = True
        self.query_one("#smartautorules-editor-title", NoMarkupStatic).update(
            f"Add an {kind.upper()} rule in your own words"
        )
        rule_input = self.query_one("#smartautorules-input", Input)
        rule_input.value = ""
        rule_input.focus()

    def _cancel_add(self) -> None:
        self._adding = None
        self.query_one("#smartautorules-editor").display = False
        self.query_one("#smartautorules-content").display = True
        self.query_one(OptionList).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        rule = event.value.strip()
        if rule and self._adding == "ask" and rule not in self.ask_rules:
            self.ask_rules.append(rule)
        elif rule and self._adding == "allow" and rule not in self.allow_rules:
            self.allow_rules.append(rule)
        self._cancel_add()
        self._refresh_options()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id is None:
            return
        if option_id.startswith("curated:"):
            rule = CURATED_ASK_RULES[int(option_id.removeprefix("curated:"))]
            if rule in self.ask_rules:
                self.ask_rules.remove(rule)
            else:
                self.ask_rules.append(rule)
            self._refresh_options()
        elif option_id == "action:add-ask":
            self._start_add("ask")
        elif option_id == "action:add-allow":
            self._start_add("allow")
        elif option_id == "action:save":
            self.action_close_or_cancel()

    def action_remove_rule(self) -> None:
        if self._adding is not None:
            return
        option_list = self.query_one(OptionList)
        if option_list.highlighted is None:
            return
        option_id = option_list.get_option_at_index(option_list.highlighted).id
        if option_id is None:
            return
        if option_id.startswith("ask:"):
            rule = self._custom_ask_rules[int(option_id.removeprefix("ask:"))]
            self.ask_rules.remove(rule)
            self._refresh_options()
        elif option_id.startswith("allow:"):
            del self.allow_rules[int(option_id.removeprefix("allow:"))]
            self._refresh_options()

    def action_close_or_cancel(self) -> None:
        if self._adding is not None:
            self._cancel_add()
            return
        self.post_message(self.RulesClosed(self.ask_rules, self.allow_rules))

    def action_close(self) -> None:
        self.action_close_or_cancel()
