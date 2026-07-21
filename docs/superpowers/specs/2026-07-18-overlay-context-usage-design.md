# Overlay Context & Usage 进度条设计

日期:2026-07-18
状态:待实现

## 目标

在 Pawgress overlay 中新增一行状态,样式仿 claude-hud statusline:

```
Context ██░░░░░░░░ 7% | Usage ██░░░░░░░░ 34% (resets in 40s)
```

- **Context**:当前会话上下文占用百分比,与 TUI 底栏一致。
- **Usage**:Mistral API 每分钟 token 限额(TPM)的已用百分比,来自
  `x-ratelimit-*` 响应头。窗口按分钟滚动,reset 显示秒级倒计时。

## 数据源(均已在代码中验证)

| 数据 | 来源 |
|---|---|
| Context 分子 | `stats.context_tokens` — `vibe/core/agent_loop/_loop.py:2229` 每次 LLM 响应后更新(prompt + completion),compaction 时清零(`compaction/manager.py:108`) |
| Context 分母 | `config.get_active_model().auto_compact_threshold`(默认 200_000,`config/_defaults.py`) |
| Usage | Mistral 每个响应带 `x-ratelimit-limit-tokens` / `x-ratelimit-remaining-tokens`(以及 requests 变体)。流式路径在 `vibe/core/llm/backend/mistral.py:413` 已能访问 `stream.response.headers`(现仅取 correlation-id) |
| Usage reset | Mistral 未文档化 reset 头;按分钟窗口用「捕获时间 + 60s」估算倒计时。若响应中存在 reset 类头则优先使用 |

## 改动

### 1. 采集 rate-limit 头 — `vibe/core/llm/backend/mistral.py`

在 streaming 响应处(`:413` 附近)读取 `x-ratelimit-limit-tokens` /
`x-ratelimit-remaining-tokens`,连同捕获时间写入 stats 新字段:

- `rate_limit_tokens_limit: int | None`
- `rate_limit_tokens_remaining: int | None`
- `rate_limit_captured_at: float | None`(`time.monotonic()`;倒计时秒数由
  app.py 在 emit 时算好写入 `usage_reset_seconds`,overlay 进程不依赖此时钟)

字段加在 `vibe/core/types.py` 的 stats 模型上。头缺失时保持 None,
overlay 端优雅降级(只显示 Context)。

### 2. 协议 — `vibe/core/pawgress/events.py`

`IslandState`(`extra="forbid"`)新增可选字段:

- `context_tokens: int | None = None`
- `context_max: int | None = None`
- `usage_used: int | None = None`(= limit − remaining)
- `usage_limit: int | None = None`
- `usage_reset_seconds: int | None = None`

### 3. 透传 — `vibe/core/pawgress/controller.py` + `vibe/cli/textual_ui/app.py`

- `GoalController.island_state()` 增加同名 kwargs 并透传。
- app.py 增加一个 helper 从 `agent_loop.stats` / `config` 组装这些参数,
  所有 `island_state(...)` 调用点带上。
- **实时更新**:复用 `app.py:828` 已有的 `stats.add_listener("context_tokens", ...)`
  机制,Pawgress 激活时同时写一条 IslandState 到 sink;做节流
  (百分比变化 ≥1% 才写)避免高频刷文件。

### 4. 渲染 — `vibe/overlay/render.py`

- HTML 版(`render_island_html`):在 meta 行附近新增一行,复用 `_bar_html()`:
  `Context` 标签用 MUTED、百分比用 GREEN;`Usage` 百分比用 BLUE,
  后接 `(resets in Ns)` MUTED。
- 纯文本版(`render_island`):同样新增一行,复用 `progress_bar()`。
- 降级规则:无 context 数据 → `Context ░░░░░░░░░░ 0%`;
  无 usage 数据 → 省略 Usage 段。
- reset 倒计时随已有的 tick 重绘自然递减,归零后保持 0 直到下一次响应头刷新。

## 不做的事(YAGNI)

- 不新增任何轮询/额外 API 请求 —— usage 只从既有响应头被动采集。
- 不做非流式路径的头采集(主循环全部走流式)。
- 不做订阅级配额(Mistral 无公开接口)。

## 测试

- `tests/` 下为 render 新增单测:有/无 context、有/无 usage、0%、100%、
  截断/取整边界(复用现有 `progress_bar` 测试风格)。
- `IslandState` 新字段的序列化往返(旧 JSONL 行无新字段仍可解析)。
- 手动验证:`stub_feed.py` 加带新字段的样例行,起 overlay 目测。
