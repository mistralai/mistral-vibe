from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from pydantic import BaseModel
import pytest

from vibe.core.tools.base import ToolError, ToolPermission
from vibe.core.tools.builtins._computer_use_trace import (
    SENTINEL,
    action_label as _action_label,
    is_meaningful as _is_meaningful,
)
from vibe.core.tools.builtins.computer_use import (
    ComputerUse,
    ComputerUseArgs,
    ComputerUseConfig,
    ComputerUseResult,
    _step_from_history_item,
)
from vibe.core.tools.permissions import PermissionScope


def _tool(**overrides: Any) -> ComputerUse:
    return cast(
        ComputerUse, ComputerUse.from_config(lambda: ComputerUseConfig(**overrides))
    )


class TestName:
    def test_registers_as_computer_use(self):
        assert ComputerUse.get_name() == "computer_use"

    def test_description_comes_from_prompt_file(self):
        assert "Drive a real Chromium browser" in ComputerUse.get_full_description()

    def test_exposes_url_task_and_intent(self):
        properties = ComputerUse.get_parameters()["properties"]
        assert {"url", "task", "intent", "max_steps", "show_browser"} <= set(properties)


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("example.com", "https://example.com"),
            ("//example.com", "https://example.com"),
            ("http://example.com", "http://example.com"),
            ("https://example.com/a?b=c", "https://example.com/a?b=c"),
        ],
    )
    def test_normalizes(self, raw: str, expected: str):
        assert ComputerUse._normalize_url(raw) == expected


class TestValidateUrl:
    def test_rejects_non_http_scheme(self):
        with pytest.raises(ToolError, match="Invalid URL scheme"):
            _tool()._validate_url("ftp://example.com")

    def test_rejects_missing_host(self):
        with pytest.raises(ToolError, match="must include a host"):
            _tool()._validate_url("https://")

    def test_accepts_https(self):
        _tool()._validate_url("https://example.com")


class TestPermission:
    def test_scopes_prompt_to_domain(self):
        context = _tool().resolve_permission(
            ComputerUseArgs(url="https://shop.example.com/x", task="buy a thing")
        )
        assert context is not None
        assert context.permission is ToolPermission.ASK
        required = context.required_permissions[0]
        assert required.scope is PermissionScope.URL_PATTERN
        assert required.invocation_pattern == "shop.example.com"

    def test_respects_always(self):
        context = _tool(permission=ToolPermission.ALWAYS).resolve_permission(
            ComputerUseArgs(url="https://example.com", task="t")
        )
        assert context is not None
        assert context.permission is ToolPermission.ALWAYS


class TestIsMeaningful:
    @pytest.mark.parametrize("value", [None, False, "", [], {}, ()])
    def test_drops_empty(self, value: Any):
        assert not _is_meaningful(value)

    @pytest.mark.parametrize("value", [0, "x", ["a"], {"a": 1}, True])
    def test_keeps_content(self, value: Any):
        assert _is_meaningful(value)

    def test_tolerates_unhashable(self):
        # Action arguments are frequently lists; a set-membership test would raise.
        assert _is_meaningful(["a", "b"])


class _Click(BaseModel):
    index: int
    label: str = ""


class _Root(BaseModel):
    click: _Click


class TestActionLabel:
    def test_renders_name_and_arguments(self):
        assert _action_label(_Root(click=_Click(index=7))) == "click — index=7"

    def test_joins_a_list_of_actions(self):
        label = _action_label([
            _Root(click=_Click(index=1)),
            _Root(click=_Click(index=2)),
        ])
        assert label == "click — index=1; click — index=2"

    def test_handles_none(self):
        assert _action_label(None) == "—"


class TestStepFromHistory:
    def test_extracts_action_thought_and_url(self):
        item = SimpleNamespace(
            model_output=SimpleNamespace(
                action=_Root(click=_Click(index=3)), next_goal="Apply the filter"
            ),
            state=SimpleNamespace(url="https://example.com/list"),
        )
        step = _step_from_history_item(item, 4)
        assert step.step == 4
        assert step.action == "click — index=3"
        assert step.thought == "Apply the filter"
        assert step.url == "https://example.com/list"

    def test_survives_missing_fields(self):
        step = _step_from_history_item(SimpleNamespace(), 1)
        assert step.action == "—"
        assert step.thought == ""
        assert step.url == ""


class TestBuildResult:
    @staticmethod
    def _history(*, steps: int, done: bool) -> dict[str, Any]:
        return {
            "steps": [
                {
                    "step": i + 1,
                    "action": f"click — index={i}",
                    "thought": "",
                    "url": f"https://example.com/{i}",
                }
                for i in range(steps)
            ],
            "is_done": done,
            "final_result": "did the thing",
        }

    def test_reports_completion_and_trace(self):
        result = ComputerUse._build_result(
            self._history(steps=3, done=True),
            "https://example.com",
            ComputerUseArgs(url="https://example.com", task="t"),
            25,
        )
        assert isinstance(result, ComputerUseResult)
        assert result.completed
        assert result.num_steps == 3
        assert result.final_url == "https://example.com/2"
        assert result.summary == "did the thing"
        assert not result.budget_exhausted

    def test_flags_budget_exhaustion(self):
        result = ComputerUse._build_result(
            self._history(steps=8, done=False),
            "https://example.com",
            ComputerUseArgs(url="https://example.com", task="t"),
            8,
        )
        assert result.budget_exhausted
        assert not result.completed

    def test_does_not_flag_exhaustion_when_done_on_last_step(self):
        result = ComputerUse._build_result(
            self._history(steps=8, done=True),
            "https://example.com",
            ComputerUseArgs(url="https://example.com", task="t"),
            8,
        )
        assert not result.budget_exhausted

    def test_falls_back_to_start_url_without_steps(self):
        result = ComputerUse._build_result(
            self._history(steps=0, done=False),
            "https://example.com",
            ComputerUseArgs(url="https://example.com", task="t"),
            25,
        )
        assert result.final_url == "https://example.com"


class TestResolveHeadless:
    def test_show_browser_true_is_headed(self):
        tool = _tool()
        assert not tool._resolve_headless(
            ComputerUseArgs(url="https://example.com", task="t", show_browser=True)
        )

    def test_show_browser_false_is_headless(self):
        tool = _tool()
        assert tool._resolve_headless(
            ComputerUseArgs(url="https://example.com", task="t", show_browser=False)
        )

    def test_env_headless_overrides(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("COMPUTER_USE_HEADLESS", "1")
        tool = _tool()
        assert tool._resolve_headless(
            ComputerUseArgs(url="https://example.com", task="t", show_browser=True)
        )


class TestWorkerRequest:
    def test_carries_prompt_and_browser_settings(self):
        request = _tool()._worker_request(
            "https://example.com",
            ComputerUseArgs(url="https://example.com", task="buy milk"),
            api_key="k",
            headless=False,
            max_steps=7,
        )
        assert "buy milk" in request["prompt"]
        assert request["api_key"] == "k"
        assert request["headless"] is False
        assert request["max_steps"] == 7


class TestParseEvent:
    def test_reads_sentinel_prefixed_json(self):
        line = (SENTINEL + '{"type": "step", "step": 2}\n').encode()
        assert ComputerUse._parse_event(line) == {"type": "step", "step": 2}

    def test_ignores_unrelated_output(self):
        assert ComputerUse._parse_event(b"INFO some library chatter\n") is None

    def test_ignores_malformed_json(self):
        assert ComputerUse._parse_event((SENTINEL + "{not json").encode()) is None


class TestHeartbeat:
    def test_reports_launching_before_first_step(self):
        message = ComputerUse._heartbeat(9.0, 0, 12)
        assert "launching browser" in message
        assert "step 1/12" in message

    def test_reports_waiting_once_steps_land(self):
        message = ComputerUse._heartbeat(9.0, 2, 12)
        assert "waiting on browser/model" in message
        assert "step 3/12" in message


class TestPrompt:
    def test_includes_url_task_and_intent(self):
        prompt = ComputerUse._build_prompt(
            "https://example.com",
            ComputerUseArgs(
                url="https://example.com", task="buy milk", intent="a shopper"
            ),
        )
        assert "https://example.com" in prompt
        assert "buy milk" in prompt
        assert "a shopper" in prompt

    def test_omits_intent_when_absent(self):
        prompt = ComputerUse._build_prompt(
            "https://example.com", ComputerUseArgs(url="https://example.com", task="t")
        )
        assert "acting for this user" not in prompt


class TestAvailability:
    def test_available_with_api_key_even_without_browser_use(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "vibe.core.tools.builtins.computer_use.importlib.util.find_spec",
            lambda _name: None,
        )
        monkeypatch.setattr(
            "vibe.core.tools.builtins.computer_use.resolve_api_key", lambda _key: "k"
        )
        assert ComputerUse.is_available()

    def test_hidden_without_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "vibe.core.tools.builtins.computer_use.importlib.util.find_spec",
            lambda _name: object(),
        )
        monkeypatch.setattr(
            "vibe.core.tools.builtins.computer_use.resolve_api_key", lambda _key: ""
        )
        assert not ComputerUse.is_available()

    def test_available_with_both(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "vibe.core.tools.builtins.computer_use.importlib.util.find_spec",
            lambda _name: object(),
        )
        monkeypatch.setattr(
            "vibe.core.tools.builtins.computer_use.resolve_api_key", lambda _key: "k"
        )
        assert ComputerUse.is_available()
