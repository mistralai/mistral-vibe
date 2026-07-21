# Overlay Context/Usage 进度条 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Pawgress overlay 中显示 `Context ██░░ 7% | Usage ██░░ 34% (resets in 40s)` 状态行,Context 实时反映会话上下文占用,Usage 来自 Mistral API 每分钟 rate-limit 响应头。

**Architecture:** rate-limit 头在 `mistral.py` 流式响应处被动采集 → 挂在 `LLMChunk` 上流经 agent loop → 写入 `AgentStats` → app.py 组装进 `IslandState` 新字段写入 JSONL sink → overlay 渲染。Context 数据直接来自 `stats.context_tokens` / `auto_compact_threshold`(与 TUI 底栏同源)。

**Tech Stack:** Python 3.12, pydantic, PySide6(overlay), pytest。测试统一 `uv run pytest ...`。

**Spec:** `docs/superpowers/specs/2026-07-18-overlay-context-usage-design.md`

## Global Constraints

- 不发起任何额外 API 请求,usage 只从既有响应头被动采集。
- 只改 mistral 流式路径,不动非流式/generic 后端。
- `IslandState` 为 `extra="forbid"`,旧 JSONL 行(无新字段)必须仍可解析。
- 进度条宽度沿用现有 10 格;颜色:Context 百分比 GREEN,Usage 百分比 BLUE,标签/reset MUTED。
- overlay 定时器 160ms/tick(`window.py`),换算常量 `TICK_SECONDS = 0.16`。
- 提交信息用 `feat:`/`test:` 前缀;仓库有 pre-commit(pyright/ruff),提交失败先看 hook 输出。

---

### Task 1: RateLimitInfo 模型 + LLMChunk / AgentStats 新字段

**Files:**
- Modify: `vibe/core/types.py`(`AgentStats` ~L50,`LLMChunk` ~L443)
- Test: `tests/core/test_rate_limit_types.py`(新建;若 `tests/core/` 不存在则建目录加空 `__init__.py`,先看现有 tests 结构决定)

**Interfaces:**
- Produces: `RateLimitInfo(limit_tokens: int, remaining_tokens: int)`(frozen BaseModel);`LLMChunk.rate_limit: RateLimitInfo | None = None`;`AgentStats.rate_limit_tokens_limit: int = 0`、`AgentStats.rate_limit_tokens_remaining: int = 0`、`AgentStats.rate_limit_captured_at: float = 0.0`

- [ ] **Step 1: 写失败测试**

```python
# tests/core/test_rate_limit_types.py
from __future__ import annotations

from vibe.core.types import AgentStats, LLMChunk, LLMMessage, RateLimitInfo, Role


def test_rate_limit_info_frozen_fields():
    info = RateLimitInfo(limit_tokens=500_000, remaining_tokens=330_000)
    assert info.limit_tokens == 500_000
    assert info.remaining_tokens == 330_000


def test_llm_chunk_rate_limit_defaults_none_and_survives_add():
    a = LLMChunk(message=LLMMessage(role=Role.assistant, content="a"))
    b = LLMChunk(
        message=LLMMessage(role=Role.assistant, content="b"),
        rate_limit=RateLimitInfo(limit_tokens=10, remaining_tokens=5),
    )
    assert a.rate_limit is None
    merged = a + b
    assert merged.rate_limit is not None
    assert merged.rate_limit.remaining_tokens == 5


def test_agent_stats_rate_limit_defaults():
    stats = AgentStats()
    assert stats.rate_limit_tokens_limit == 0
    assert stats.rate_limit_tokens_remaining == 0
    assert stats.rate_limit_captured_at == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/core/test_rate_limit_types.py -v`
Expected: FAIL(ImportError: cannot import name 'RateLimitInfo')

- [ ] **Step 3: 实现**

`vibe/core/types.py`:在 `LLMChunk` 定义之前加:

```python
class RateLimitInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    limit_tokens: int
    remaining_tokens: int
```

`LLMChunk` 加字段与 `__add__` 传播(仿 `correlation_id`):

```python
class LLMChunk(BaseModel):
    model_config = ConfigDict(frozen=True)
    message: LLMMessage
    usage: LLMUsage | None = None
    correlation_id: str | None = None
    rate_limit: RateLimitInfo | None = None
    stop: StopInfo | None = None

    def __add__(self, other: LLMChunk) -> LLMChunk:
        ...
        return LLMChunk(
            message=self.message + other.message,
            usage=new_usage,
            correlation_id=other.correlation_id or self.correlation_id,
            rate_limit=other.rate_limit or self.rate_limit,
            stop=other.stop or self.stop,  # 保持现有字段原样,只插入 rate_limit
        )
```

(注意:`__add__` 现有其余字段的合并逻辑保持原样,只新增 `rate_limit=` 一行。)

`AgentStats` 在 `context_tokens` 附近加:

```python
    rate_limit_tokens_limit: int = 0
    rate_limit_tokens_remaining: int = 0
    rate_limit_captured_at: float = 0.0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/core/test_rate_limit_types.py -v`
Expected: PASS(3 个)

- [ ] **Step 5: Commit**

```bash
git add vibe/core/types.py tests/core/
git commit -m "feat: add RateLimitInfo to LLMChunk and AgentStats"
```

---

### Task 2: mistral.py 采集 x-ratelimit 头

**Files:**
- Modify: `vibe/core/llm/backend/mistral.py`(`complete_streaming` ~L413)
- Test: `tests/backend/test_mistral_rate_limit.py`(新建,`tests/backend/` 已存在)

**Interfaces:**
- Consumes: `RateLimitInfo`(Task 1)
- Produces: 模块级函数 `parse_rate_limit_headers(headers: Mapping[str, str]) -> RateLimitInfo | None`;`complete_streaming` yield 的每个 `LLMChunk` 带 `rate_limit`

- [ ] **Step 1: 写失败测试**

```python
# tests/backend/test_mistral_rate_limit.py
from __future__ import annotations

from vibe.core.llm.backend.mistral import parse_rate_limit_headers


def test_parses_standard_headers():
    headers = {
        "x-ratelimit-limit-tokens": "500000",
        "x-ratelimit-remaining-tokens": "330000",
    }
    info = parse_rate_limit_headers(headers)
    assert info is not None
    assert info.limit_tokens == 500_000
    assert info.remaining_tokens == 330_000


def test_parses_bysize_variant():
    headers = {
        "x-ratelimitbysize-limit": "500000",
        "x-ratelimitbysize-remaining": "120000",
    }
    info = parse_rate_limit_headers(headers)
    assert info is not None
    assert info.limit_tokens == 500_000
    assert info.remaining_tokens == 120_000


def test_returns_none_when_missing_or_malformed():
    assert parse_rate_limit_headers({}) is None
    assert parse_rate_limit_headers({"x-ratelimit-limit-tokens": "abc"}) is None
    assert (
        parse_rate_limit_headers({"x-ratelimit-limit-tokens": "100"}) is None
    )  # 缺 remaining
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/backend/test_mistral_rate_limit.py -v`
Expected: FAIL(ImportError: cannot import name 'parse_rate_limit_headers')

- [ ] **Step 3: 实现**

`mistral.py` 模块级(import `RateLimitInfo` 加入现有 `vibe.core.types` import 列表;`Mapping` 来自 `collections.abc`):

```python
_RATE_LIMIT_KEY_PAIRS: tuple[tuple[str, str], ...] = (
    ("x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens"),
    ("x-ratelimitbysize-limit", "x-ratelimitbysize-remaining"),
)


def parse_rate_limit_headers(headers: Mapping[str, str]) -> RateLimitInfo | None:
    for limit_key, remaining_key in _RATE_LIMIT_KEY_PAIRS:
        limit_raw = headers.get(limit_key)
        remaining_raw = headers.get(remaining_key)
        if limit_raw is None or remaining_raw is None:
            continue
        try:
            return RateLimitInfo(
                limit_tokens=int(limit_raw), remaining_tokens=int(remaining_raw)
            )
        except ValueError:
            continue
    return None
```

`complete_streaming` 中 L413 后:

```python
            correlation_id = stream.response.headers.get("mistral-correlation-id")
            rate_limit = parse_rate_limit_headers(stream.response.headers)
```

并在 yield 的 `LLMChunk(...)` 构造里加 `rate_limit=rate_limit,`(与 `correlation_id=correlation_id` 并列)。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/backend/test_mistral_rate_limit.py -v`
Expected: PASS(3 个)。再跑 `uv run pytest tests/backend -x -q` 确认没破坏既有后端测试。

- [ ] **Step 5: Commit**

```bash
git add vibe/core/llm/backend/mistral.py tests/backend/test_mistral_rate_limit.py
git commit -m "feat: capture Mistral x-ratelimit headers on streaming responses"
```

---

### Task 3: agent loop 把 rate_limit 写入 stats

**Files:**
- Modify: `vibe/core/agent_loop/_loop.py`(流式循环 ~L2168-2201,`_update_stats` ~L2223)
- Test: `tests/agent_loop/test_update_stats_rate_limit.py`(新建;`tests/agent_loop/` 已存在)

**Interfaces:**
- Consumes: `LLMChunk.rate_limit`、`AgentStats.rate_limit_*`(Task 1/2)
- Produces: `_update_stats(usage, time_seconds, rate_limit: RateLimitInfo | None = None)`;调用后 `stats.rate_limit_tokens_limit/remaining/captured_at` 被更新

- [ ] **Step 1: 写失败测试**

```python
# tests/agent_loop/test_update_stats_rate_limit.py
from __future__ import annotations

from vibe.core.agent_loop._loop import AgentLoop
from vibe.core.types import AgentStats, LLMUsage, RateLimitInfo


def _bare_loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)  # 绕过重量级 __init__,只测 _update_stats
    loop.stats = AgentStats()
    return loop


def test_update_stats_records_rate_limit():
    loop = _bare_loop()
    loop._update_stats(
        usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
        time_seconds=1.0,
        rate_limit=RateLimitInfo(limit_tokens=500_000, remaining_tokens=330_000),
    )
    assert loop.stats.rate_limit_tokens_limit == 500_000
    assert loop.stats.rate_limit_tokens_remaining == 330_000
    assert loop.stats.rate_limit_captured_at > 0


def test_update_stats_none_rate_limit_keeps_previous():
    loop = _bare_loop()
    loop.stats.rate_limit_tokens_limit = 100
    loop.stats.rate_limit_tokens_remaining = 40
    loop.stats.rate_limit_captured_at = 123.0
    loop._update_stats(
        usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        time_seconds=1.0,
        rate_limit=None,
    )
    assert loop.stats.rate_limit_tokens_limit == 100
    assert loop.stats.rate_limit_tokens_remaining == 40
    assert loop.stats.rate_limit_captured_at == 123.0
```

（若 `AgentLoop.__new__` 因 slots/属性问题不可行,退路:把 stats 更新逻辑抽成模块级函数 `apply_rate_limit_to_stats(stats, rate_limit)` 并直接测它——两个测试断言不变。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agent_loop/test_update_stats_rate_limit.py -v`
Expected: FAIL(TypeError: unexpected keyword argument 'rate_limit')

- [ ] **Step 3: 实现**

`_loop.py` 流式循环:在 `chunk_agg: LLMChunk | None = None`(~L2168)旁加 `rate_limit: RateLimitInfo | None = None`;循环体内(`if chunk.correlation_id:` 旁)加:

```python
                if chunk.rate_limit is not None:
                    rate_limit = chunk.rate_limit
```

L2201 调用改为:

```python
            self._update_stats(
                usage=usage, time_seconds=end_time - start_time, rate_limit=rate_limit
            )
```

`_update_stats`(注意:rate-limit 字段先赋值,`context_tokens` 保持最后,这样 `context_tokens` 的 listener 触发时能看到最新 rate 数据):

```python
    def _update_stats(
        self,
        usage: LLMUsage,
        time_seconds: float,
        rate_limit: RateLimitInfo | None = None,
    ) -> None:
        self.stats.last_turn_duration = time_seconds
        self.stats.last_turn_prompt_tokens = usage.prompt_tokens
        self.stats.last_turn_completion_tokens = usage.completion_tokens
        self.stats.session_prompt_tokens += usage.prompt_tokens
        self.stats.session_completion_tokens += usage.completion_tokens
        if rate_limit is not None:
            self.stats.rate_limit_tokens_limit = rate_limit.limit_tokens
            self.stats.rate_limit_tokens_remaining = rate_limit.remaining_tokens
            self.stats.rate_limit_captured_at = time.monotonic()
        self.stats.context_tokens = usage.prompt_tokens + usage.completion_tokens
        if time_seconds > 0 and usage.completion_tokens > 0:
            self.stats.tokens_per_second = usage.completion_tokens / time_seconds
```

`RateLimitInfo` 加进 `_loop.py` 的 `vibe.core.types` import;`time` 已 import(L2167 用了 `time.perf_counter`)。
另外检查 `_update_stats` 是否还有其他调用点(`grep -n "_update_stats" vibe/core/agent_loop/_loop.py`),其余调用点不传 `rate_limit` 即可(默认 None)。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/agent_loop/test_update_stats_rate_limit.py -v`
Expected: PASS(2 个)。再跑 `uv run pytest tests/agent_loop -x -q`。

- [ ] **Step 5: Commit**

```bash
git add vibe/core/agent_loop/_loop.py tests/agent_loop/test_update_stats_rate_limit.py
git commit -m "feat: propagate rate-limit info from stream chunks into AgentStats"
```

---

### Task 4: IslandState 协议新字段

**Files:**
- Modify: `vibe/core/pawgress/events.py`
- Test: `tests/pawgress/test_events.py`(追加)

**Interfaces:**
- Produces: `IslandState` 新可选字段 `context_tokens: int | None = None`、`context_max: int | None = None`、`usage_used: int | None = None`、`usage_limit: int | None = None`、`usage_reset_seconds: int | None = None`

- [ ] **Step 1: 写失败测试**(追加到 `tests/pawgress/test_events.py`)

```python
def test_island_state_context_usage_roundtrip():
    state = IslandState(
        goal="g",
        state=IslandStatus.WORKING,
        context_tokens=14_000,
        context_max=200_000,
        usage_used=170_000,
        usage_limit=500_000,
        usage_reset_seconds=40,
    )
    parsed = parse_island_state(encode_jsonl(state))
    assert parsed.context_tokens == 14_000
    assert parsed.context_max == 200_000
    assert parsed.usage_used == 170_000
    assert parsed.usage_limit == 500_000
    assert parsed.usage_reset_seconds == 40


def test_island_state_old_lines_without_new_fields_still_parse():
    line = '{"type": "island_state", "goal": "g", "state": "working"}'
    parsed = parse_island_state(line)
    assert parsed.context_tokens is None
    assert parsed.usage_limit is None
```

(import 按该文件现有风格补齐:`IslandState, IslandStatus, encode_jsonl, parse_island_state`。)

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/pawgress/test_events.py -v -k context_usage`
Expected: FAIL(ValidationError: extra inputs not permitted)

- [ ] **Step 3: 实现**

`IslandState` 在 `evidence` 之前加:

```python
    context_tokens: int | None = None
    context_max: int | None = None
    usage_used: int | None = None
    usage_limit: int | None = None
    usage_reset_seconds: int | None = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/pawgress/test_events.py -v`
Expected: 全部 PASS(含旧测试)

- [ ] **Step 5: Commit**

```bash
git add vibe/core/pawgress/events.py tests/pawgress/test_events.py
git commit -m "feat: add context/usage fields to IslandState"
```

---

### Task 5: GoalController.island_state 透传

**Files:**
- Modify: `vibe/core/pawgress/controller.py`(`island_state` ~L85)
- Test: `tests/pawgress/test_controller.py`(追加)

**Interfaces:**
- Consumes: Task 4 字段
- Produces: `island_state(*, cost=None, budget=None, detail="", elapsed=None, context_tokens=None, context_max=None, usage_used=None, usage_limit=None, usage_reset_seconds=None)`

- [ ] **Step 1: 写失败测试**(追加到 `tests/pawgress/test_controller.py`)

```python
def test_island_state_passes_context_and_usage_through():
    controller = GoalController(make_goal())

    state = controller.island_state(
        context_tokens=14_000,
        context_max=200_000,
        usage_used=170_000,
        usage_limit=500_000,
        usage_reset_seconds=40,
    )

    assert state.context_tokens == 14_000
    assert state.context_max == 200_000
    assert state.usage_used == 170_000
    assert state.usage_limit == 500_000
    assert state.usage_reset_seconds == 40


def test_island_state_context_usage_default_none():
    state = GoalController(make_goal()).island_state()
    assert state.context_tokens is None
    assert state.usage_limit is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/pawgress/test_controller.py -v -k context`
Expected: FAIL(TypeError: unexpected keyword argument 'context_tokens')

- [ ] **Step 3: 实现**

`island_state` 签名追加 5 个 keyword-only 参数(全部默认 `None`),`IslandState(...)` 构造里逐一传入:

```python
    def island_state(
        self,
        *,
        cost: float | None = None,
        budget: float | None = None,
        detail: str = "",
        elapsed: str | None = None,
        context_tokens: int | None = None,
        context_max: int | None = None,
        usage_used: int | None = None,
        usage_limit: int | None = None,
        usage_reset_seconds: int | None = None,
    ) -> IslandState:
        ...
        return IslandState(
            ...,  # 现有参数原样
            context_tokens=context_tokens,
            context_max=context_max,
            usage_used=usage_used,
            usage_limit=usage_limit,
            usage_reset_seconds=usage_reset_seconds,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/pawgress/test_controller.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/core/pawgress/controller.py tests/pawgress/test_controller.py
git commit -m "feat: thread context/usage stats through GoalController.island_state"
```

---

### Task 6: render.py 渲染 Context/Usage 行

**Files:**
- Modify: `vibe/overlay/render.py`
- Test: `tests/pawgress/test_render.py`(新建)

**Interfaces:**
- Consumes: `IslandState` 新字段(Task 4)、现有 `progress_bar`/`_bar_html`/`_span`/`_pct`
- Produces: `_pct(current: int, total: int) -> int`;`context_usage_line(state, age_seconds: int = 0) -> str`(纯文本);`context_usage_html(state, age_seconds: int = 0) -> str`;`render_island_html(..., age_seconds: int = 0)` 新增参数

- [ ] **Step 1: 写失败测试**

```python
# tests/pawgress/test_render.py
from __future__ import annotations

from vibe.core.pawgress.events import IslandState, IslandStatus
from vibe.overlay.render import (
    _pct,
    context_usage_html,
    context_usage_line,
    render_island,
    render_island_html,
)


def make_state(**kwargs) -> IslandState:
    base: dict[str, object] = {"goal": "g", "state": IslandStatus.WORKING}
    base.update(kwargs)
    return IslandState.model_validate(base)


def test_pct_clamps_and_rounds():
    assert _pct(0, 0) == 0
    assert _pct(7, 100) == 7
    assert _pct(150, 100) == 100
    assert _pct(-5, 100) == 0
    assert _pct(1, 3) == 33


def test_line_without_any_data_shows_zero_context():
    line = context_usage_line(make_state())
    assert line == "Context ░░░░░░░░░░ 0%"


def test_line_with_context_only_omits_usage():
    line = context_usage_line(make_state(context_tokens=14_000, context_max=200_000))
    assert line.startswith("Context ")
    assert "7%" in line
    assert "Usage" not in line


def test_line_with_usage_and_reset():
    line = context_usage_line(
        make_state(
            context_tokens=14_000,
            context_max=200_000,
            usage_used=170_000,
            usage_limit=500_000,
            usage_reset_seconds=40,
        )
    )
    assert "Context" in line and "7%" in line
    assert "Usage" in line and "34%" in line
    assert "(resets in 40s)" in line


def test_reset_countdown_ages_and_floors_at_zero():
    state = make_state(usage_used=1, usage_limit=10, usage_reset_seconds=40)
    assert "(resets in 25s)" in context_usage_line(state, age_seconds=15)
    assert "(resets in 0s)" in context_usage_line(state, age_seconds=999)


def test_render_island_includes_context_line():
    text = render_island(make_state(context_tokens=14_000, context_max=200_000), "cat")
    assert "Context" in text
    assert "7%" in text


def test_html_variants():
    html_row = context_usage_html(
        make_state(
            context_tokens=14_000,
            context_max=200_000,
            usage_used=170_000,
            usage_limit=500_000,
            usage_reset_seconds=40,
        )
    )
    assert "Context" in html_row and "7%" in html_row
    assert "Usage" in html_row and "34%" in html_row
    full = render_island_html(
        make_state(context_tokens=14_000, context_max=200_000), "cat"
    )
    assert "Context" in full
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/pawgress/test_render.py -v`
Expected: FAIL(ImportError: cannot import name '_pct')

- [ ] **Step 3: 实现**

`render.py` 在 `progress_bar` 之后加:

```python
def _pct(current: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, round(100 * current / total)))


def _reset_suffix(state: IslandState, age_seconds: int) -> str:
    if state.usage_reset_seconds is None:
        return ""
    remaining = max(0, state.usage_reset_seconds - age_seconds)
    return f" (resets in {remaining}s)"


def context_usage_line(state: IslandState, age_seconds: int = 0) -> str:
    ctx = state.context_tokens or 0
    ctx_max = state.context_max or 0
    line = f"Context {progress_bar(ctx, ctx_max)} {_pct(ctx, ctx_max)}%"
    if state.usage_limit:
        used = state.usage_used or 0
        line += (
            f" | Usage {progress_bar(used, state.usage_limit)}"
            f" {_pct(used, state.usage_limit)}%"
        )
        line += _reset_suffix(state, age_seconds)
    return line
```

HTML 版(放在 `_bar_html` 之后;注意 `context_usage_html` 需要在 `_span`/`_bar_html` 定义之后):

```python
def context_usage_html(state: IslandState, age_seconds: int = 0) -> str:
    ctx = state.context_tokens or 0
    ctx_max = state.context_max or 0
    row = (
        _span("Context ", MUTED)
        + _bar_html(ctx, ctx_max, GREEN)
        + _span(f" {_pct(ctx, ctx_max)}%", GREEN)
    )
    if state.usage_limit:
        used = state.usage_used or 0
        row += (
            _span("  |  ", MUTED)
            + _span("Usage ", MUTED)
            + _bar_html(used, state.usage_limit, BLUE)
            + _span(f" {_pct(used, state.usage_limit)}%", BLUE)
            + _span(_reset_suffix(state, age_seconds), MUTED)
        )
    return row
```

接线:
- `render_island(state, cat_frame)`:在 meta 段之前插入 `lines.append(context_usage_line(state))`。
- `render_island_html(state, cat_frame, tick=0, with_buttons=True, age_seconds=0)`:签名加 `age_seconds: int = 0`;在 `meta = _meta_line(state)` 之前插入 `rows.append(context_usage_html(state, age_seconds))`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/pawgress/test_render.py -v`
Expected: PASS(7 个)

- [ ] **Step 5: Commit**

```bash
git add vibe/overlay/render.py tests/pawgress/test_render.py
git commit -m "feat: render Context/Usage progress line in overlay"
```

---

### Task 7: window.py 倒计时接线

**Files:**
- Modify: `vibe/overlay/window.py`(`__init__` ~L87/L134、`update_state` ~L140、`_render` ~L178)

**Interfaces:**
- Consumes: `render_island_html(..., age_seconds=...)`(Task 6)
- Produces: overlay 显示的 reset 秒数随 tick 递减(160ms/tick)

- [ ] **Step 1: 实现**(纯接线,倒计时数学已在 Task 6 测过;Qt 窗口无单测,手动验证在 Task 9)

模块级常量:`TICK_SECONDS = 0.16`(与 `self._timer.start(160)` 对应)。

- `__init__`:`self._ticks = 0` 旁加 `self._state_ticks = 0`。
- `update_state`:开头加 `self._state_ticks = self._ticks`。
- `_render` 中所有 `render_island_html(...)` 调用(grep 确认全部调用点)追加参数:

```python
age_seconds=int((self._ticks - self._state_ticks) * TICK_SECONDS),
```

- [ ] **Step 2: 静态检查 + 全量 pawgress 测试**

Run: `uv run pyright vibe/overlay/window.py 2>/dev/null || uv run python -c "import vibe.overlay.window"`(无 GUI 环境下至少 import 成功)
Run: `uv run pytest tests/pawgress -q`
Expected: 无类型/导入错误,测试 PASS

- [ ] **Step 3: Commit**

```bash
git add vibe/overlay/window.py
git commit -m "feat: tick down usage reset countdown in overlay window"
```

---

### Task 8: app.py 组装数据 + 实时发射(节流)

**Files:**
- Modify: `vibe/cli/textual_ui/app.py`(类属性 ~L487、`on_mount` ~L822-829、pawgress 区 ~L3390-3600)

**Interfaces:**
- Consumes: `AgentStats.rate_limit_*`(Task 3)、`island_state` kwargs(Task 5)
- Produces: `_pawgress_stats_kwargs() -> dict[str, int]`;`_emit_pawgress_stats() -> None`;所有 `island_state(...)` 调用带上 stats kwargs

- [ ] **Step 1: 实现类属性与 helper**

类属性区(L486 之后)加:

```python
    _pawgress_last_ctx_pct: int = -1
```

在 `_update_pawgress_status`(~L3489)附近加两个方法:

```python
    def _pawgress_stats_kwargs(self) -> dict[str, int]:
        stats = self.agent_loop.stats
        kwargs: dict[str, int] = {
            "context_tokens": stats.context_tokens,
            "context_max": self.config.get_active_model().auto_compact_threshold,
        }
        if stats.rate_limit_tokens_limit > 0:
            kwargs["usage_used"] = max(
                stats.rate_limit_tokens_limit - stats.rate_limit_tokens_remaining, 0
            )
            kwargs["usage_limit"] = stats.rate_limit_tokens_limit
            kwargs["usage_reset_seconds"] = max(
                0, 60 - int(time.monotonic() - stats.rate_limit_captured_at)
            )
        return kwargs

    def _emit_pawgress_stats(self) -> None:
        controller = self._pawgress
        if (
            controller is None
            or controller.goal.completed
            or self._pawgress_approval_id is not None
        ):
            return
        kwargs = self._pawgress_stats_kwargs()
        ctx_max = kwargs.get("context_max", 0)
        pct = round(100 * kwargs["context_tokens"] / ctx_max) if ctx_max > 0 else 0
        if pct == self._pawgress_last_ctx_pct:
            return
        self._pawgress_last_ctx_pct = pct
        self._pawgress_sink.write(controller.island_state(**kwargs))
```

确认 `import time` 已存在于 app.py(没有则加)。
守卫说明:审批等待期间(`_pawgress_approval_id is not None`)不发,避免把 WAITING 状态的 island 覆盖成 WORKING。

- [ ] **Step 2: 挂到已有 listener**

`on_mount` 的 `update_context_progress`(L822)末尾加一行——**不要**再注册新 listener(`add_listener` 按 attr 名存 dict,会覆盖既有 ContextProgress listener):

```python
        def update_context_progress(stats: AgentStats) -> None:
            context_progress.tokens = TokenState(
                max_tokens=self.config.get_active_model().auto_compact_threshold,
                current_tokens=stats.context_tokens,
            )
            self._emit_pawgress_stats()
```

- [ ] **Step 3: 各 island_state 调用点带上 kwargs**

以下调用点(行号以当前 HEAD 为准,grep `island_state(` 核对)全部加 `**self._pawgress_stats_kwargs()`:

- L3393:`controller.island_state(detail="Goal set", **self._pawgress_stats_kwargs())`
- L3463(`_run_pawgress_turn_end`):`controller.island_state(**self._pawgress_stats_kwargs())`
- L3560(`_poll_pawgress_control` 尾部):同上
- L3584(`_pawgress_announce_approval`):`controller.island_state(detail=..., **self._pawgress_stats_kwargs())`
- L3600(`_pawgress_clear_approval`):同 L3463

- [ ] **Step 4: 静态检查 + 回归**

Run: `uv run python -c "import vibe.cli.textual_ui.app"` 和 `uv run pytest tests/pawgress tests/cli -x -q`
Expected: import 无错,测试 PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/cli/textual_ui/app.py
git commit -m "feat: emit live context/usage stats to pawgress overlay sink"
```

---

### Task 9: stub_feed 样例 + 手动验证 + 全量回归

**Files:**
- Modify: `stub_feed.py`

**Interfaces:**
- Consumes: `IslandState` 新字段(Task 4)、overlay 渲染(Task 6/7)

- [ ] **Step 1: stub_feed.py 加新字段**

给脚本里各 `IslandState(...)` 加上递增的 context/usage 数据模拟真实会话,例如第一帧:

```python
            context_tokens=6_000,
            context_max=200_000,
            usage_used=40_000,
            usage_limit=500_000,
            usage_reset_seconds=55,
```

后续帧 `context_tokens` 递增(6k → 14k → 30k → …),`usage_used` 递增、`usage_reset_seconds` 递减(55 → 40 → 20 → 58 → …模拟窗口滚动),COMPLETED 帧给较大值(如 60k / 30%)。

- [ ] **Step 2: 手动验证 overlay**

Run: `uv run python stub_feed.py | uv run python -m vibe.overlay`
Expected: overlay 中出现 `Context ██░░░░░░░░ N% | Usage ██░░░░░░░░ M% (resets in Ks)` 行,百分比随帧变化,reset 秒数在两帧之间随 tick 递减;Context 绿色、Usage 蓝色。

- [ ] **Step 3: 全量回归**

Run: `uv run pytest tests -x -q --ignore=tests/e2e`
Expected: PASS(e2e 需要真实 API,跳过)

- [ ] **Step 4: Commit**

```bash
git add stub_feed.py
git commit -m "test: exercise context/usage bars in stub feed"
```
