"""vibe/core/plugins/agent_loop_patch.py

─────────────────────────────────────────────────────────────────────────────
Minimal diff / patch guide for wiring the plugin system into AgentLoop.

This file is NOT a standalone module — it documents the changes needed in
vibe/core/agent_loop.py (the existing file in the Vibe codebase).

Apply these changes once to agent_loop.py.  The diff is presented as
clearly annotated code blocks so it can also serve as a code-review guide.

─────────────────────────────────────────────────────────────────────────────
CHANGE 1 — imports (add at the top of agent_loop.py)
─────────────────────────────────────────────────────────────────────────────

    # ── Plugin system ─────────────────────────────────────────────────────────
    from vibe.core.plugins import PluginContext, PluginManager, PluginMiddleware

─────────────────────────────────────────────────────────────────────────────
CHANGE 2 — AgentLoop.__init__  (add inside __init__, after existing setup)
─────────────────────────────────────────────────────────────────────────────

    # Plugin system
    self._plugin_context = PluginContext(
        workdir=Path(config.effective_workdir),
        config=config,
    )
    self._plugin_manager = PluginManager(config, self._plugin_context)
    self._plugin_middleware = PluginMiddleware(
        self._plugin_manager, self._plugin_context
    )
    # Insert plugin middleware into the pipeline
    self.middleware_pipeline.add(self._plugin_middleware)
    # Patch _execute_tool to intercept tool calls/results
    self._plugin_middleware.patch_agent_loop(self)

─────────────────────────────────────────────────────────────────────────────
CHANGE 3 — AgentLoop.start() or equivalent async startup method
─────────────────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        # ... existing startup code ...

        # ── NEW: discover and set up plugins ─────────────────────────────────
        await self._plugin_manager.discover_and_setup()

─────────────────────────────────────────────────────────────────────────────
CHANGE 4 — AgentLoop.stop() or equivalent shutdown method
─────────────────────────────────────────────────────────────────────────────

    async def stop(self) -> None:
        # ── NEW: tear down plugins first ──────────────────────────────────────
        await self._plugin_manager.teardown_all()

        # ... existing shutdown code ...

─────────────────────────────────────────────────────────────────────────────
CHANGE 5 — Auto-diagnostics injection  [OPTIONAL — already handled]
─────────────────────────────────────────────────────────────────────────────
This change is NO LONGER REQUIRED.

PluginMiddleware._wrapped() already appends the formatted diagnostics block
to the tool result string before returning it to AgentLoop.  The string is
passed via context.extra["lsp_diagnostics_output"] and consumed atomically
inside the wrapper — no change to agent_loop.py is needed.

If you want to additionally emit a dedicated UI event (e.g. to render errors
in a separate Textual panel), you can add this OPTIONAL block inside
agent_loop.py's tool execution section:

    # OPTIONAL — emit a separate UI event for LSP diagnostics
    lsp_diag_output = self._plugin_context.extra.get("lsp_diagnostics_output")
    if lsp_diag_output:
        yield DiagnosticsEvent(content=lsp_diag_output)  # custom event type

Without this block, diagnostics still reach the user because they are
appended directly to the tool result rendered in the terminal.

─────────────────────────────────────────────────────────────────────────────
CHANGE 6 — VibeConfig additions (vibe/core/config.py)
─────────────────────────────────────────────────────────────────────────────
Add these fields to the VibeConfig dataclass / Pydantic model:

    # Plugin system
    plugin_paths: list[str] = field(default_factory=list)
    enabled_plugins: list[str] = field(default_factory=list)
    disabled_plugins: list[str] = field(default_factory=list)

And the corresponding TOML schema (for documentation / validation):

    # ~/.vibe/config.toml

    # Paths to additional plugin directories (scanned for plugin.py files)
    plugin_paths = []

    # Whitelist: only these plugins will be loaded (empty = all)
    enabled_plugins = []

    # Blacklist: these plugins will NOT be loaded
    disabled_plugins = []

    # Example: disable the built-in LSP plugin
    # disabled_plugins = ["lsp"]

─────────────────────────────────────────────────────────────────────────────
SUMMARY of touched files
─────────────────────────────────────────────────────────────────────────────

  vibe/core/agent_loop.py    → Changes 1–5 above
  vibe/core/config.py        → Change 6 (new fields)

New files added (no existing file modified):

  vibe/core/plugins/__init__.py
  vibe/core/plugins/base.py
  vibe/core/plugins/manager.py
  vibe/core/plugins/middleware.py
  vibe/core/plugins/builtin/__init__.py
  vibe/core/plugins/builtin/lsp/__init__.py
  vibe/core/plugins/builtin/lsp/plugin.py
  vibe/core/plugins/builtin/lsp/lsp_client.py
  vibe/core/plugins/builtin/lsp/registry.py
  vibe/core/plugins/builtin/lsp/tools.py
"""
