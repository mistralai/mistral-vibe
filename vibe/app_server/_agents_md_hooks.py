"""Builtin post-tool hook that lazily injects subdirectory AGENTS.md docs.

The Unified Harness core never loads AGENTS.md docs; the host composes the
startup section (user doc plus the project-root-to-trust-root chain) into the
session system instructions. The legacy ``read_file`` tool additionally
discovered AGENTS.md files between a read file's parent and its project root
and appended them to the tool result once per directory — lazy discovery that
has no runtime equivalent. This builtin hook restores that parity: every
unified session's Core binds it, and the Runtime appends newly discovered docs
to ``file_system.read_file`` results with the same rendering and
once-per-directory dedup as the legacy tool.

The handler registers Host-globally (``configure_hook_handlers``), the channel
for always-bound builtins: per-session ``hook_handlers`` are the *foreign*
hooks, and ids there surface public run notices, which a silent builtin must
not. The binding rides the session's compiled hooks so the Core dispatches it
and persists it for resume; subagent Cores inherit capabilities, so they
dispatch it too. The handler rebuilds a per-session
``HarnessFilesManager`` view from the session workspace (its additional roots
minus the cwd, so the trust gate stays in charge of the cwd) and dedups per
session id, so one Host-global closure serves every session and subagent.

A session whose tools run in a sandbox reads the docs through its Sandbox
Adapter instead, between the read file's directory and the open root holding
it, with the same rendering and dedup. Each of those reads is a round trip to
the sandbox, so the session remembers which directories it has looked in and
does not look again, with or without a doc there. A ``write_file`` or
``search_replace`` on an AGENTS.md makes its directory unchecked again, and a
``bash`` command whose text names AGENTS.md makes every directory unchecked
again, so a doc the model writes is found on its next read below it. A doc
that appears any other way (a script, ``git checkout``, an archive) in a
directory already looked in is not found. A host session reads from its own
disk, which costs little, and keeps looking every time.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from mistralai_vibe_local_harness.protocol import (
    RustAlwaysHookSelector,
    RustHarnessHookBinding,
    RustPostToolCallHookInput,
    RustPostToolCallHookResult,
    RustPostToolCallOutput,
    RustRuntimeBuiltinToolCall,
    RustTextContentBlock,
    RustToolResult,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.vibe import (
    CompiledHooks,
    HookContext,
    HookHandlers,
    PostToolCallHookHandler,
    sandbox_path,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.paths.conventions import AGENTS_MD_FILENAME
from vibe.utils import VIBE_WARNING_TAG

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import SandboxAdapter

AGENTS_MD_HOOK_BINDING_ID = "builtin:agents_md"
_READ_FILE_TOOL_NAME = "file_system.read_file"
_BASH_TOOL_NAME = "file_system.bash"
# The builtins that write a file, with the argument naming it.
_WRITE_TOOL_PATH_ARGUMENTS = {
    "file_system.write_file": "path",
    "file_system.search_replace": "file_path",
}

_AGENTS_MD_SECTION = "Contents of {directory}/AGENTS.md (project instructions for this directory):\n\n{content}"


def _passthrough(tool_result: RustToolResult) -> RustPostToolCallHookResult:
    return RustPostToolCallHookResult(
        output=RustPostToolCallOutput(tool_result=tool_result)
    )


def _session_files(
    harness_files: HarnessFilesManager, context: HookContext
) -> HarnessFilesManager:
    # The Session workspace always lists the cwd among its roots (it is the
    # file sandbox), but the session manager must never receive it there: a
    # listed cwd becomes a session root, and ``project_roots`` returns the
    # listed roots even when the tree is untrusted, which would inject
    # AGENTS.md docs past the trust gate. ``_build_session_config`` passes
    # only ``options.workspace_roots`` for the same reason — the invariant
    # ("never list the session cwd among ``for_session`` workspace roots")
    # therefore lives at two call sites. The durable home would be
    # ``for_session`` itself ignoring a listed root equal to the cwd; until
    # then, keep the two filters in sync.
    workspace = context.config.workspace
    session_cwd = workspace.cwd
    listed_roots = [
        root for root in workspace.roots if root.expanduser().resolve() != session_cwd
    ]
    return harness_files.for_session(session_cwd, workspace_roots=listed_roots)


def _read_file_path(
    call: RustRuntimeBuiltinToolCall, context: HookContext
) -> Path | None:
    raw_path = call.arguments.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = context.config.workspace.cwd / path
    try:
        return path.resolve()
    except (OSError, ValueError):
        return None


async def _sandbox_docs(
    sandbox: SandboxAdapter,
    call: RustRuntimeBuiltinToolCall,
    context: HookContext,
    checked: set[Path],
) -> list[tuple[Path, str]]:
    from vibe.app_server._sandbox_workspace import find_sandbox_subdirectory_agents_md

    raw_path = call.arguments.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return []
    workspace = context.config.workspace
    path = sandbox_path(raw_path, cwd=workspace.cwd)
    return await find_sandbox_subdirectory_agents_md(
        sandbox, path, workspace.roots, checked=checked
    )


def _forget_changed_lookups(
    checked: set[Path], call: RustRuntimeBuiltinToolCall, context: HookContext
) -> None:
    """Uncheck the sandbox directories where ``call`` may have added a doc.

    Only Vibe's own writes and a ``bash`` command naming AGENTS.md are seen;
    see the module docstring. The name is compared without case, as a
    case-insensitive file system would read either spelling.
    """
    path_argument = _WRITE_TOOL_PATH_ARGUMENTS.get(call.name)
    if path_argument is not None:
        raw_path = call.arguments.get(path_argument)
        if not isinstance(raw_path, str) or not raw_path.strip():
            return
        path = sandbox_path(raw_path.strip(), cwd=context.config.workspace.cwd)
        if path.name.casefold() == AGENTS_MD_FILENAME.casefold():
            checked.discard(path.parent)
        return
    if call.name != _BASH_TOOL_NAME:
        return
    command = call.arguments.get("command")
    if isinstance(command, str) and AGENTS_MD_FILENAME.casefold() in command.casefold():
        checked.clear()


async def _keyed_docs(
    harness_files: HarnessFilesManager,
    call: RustRuntimeBuiltinToolCall,
    context: HookContext,
    checked: set[Path],
) -> list[tuple[str, Path, str]]:
    """The docs a read file brings in, each with the key it dedups under."""
    sandbox = context.config.sandbox
    if sandbox is not None:
        docs = await _sandbox_docs(sandbox, call, context, checked)
        return [(str(directory), directory, content) for directory, content in docs]
    path = _read_file_path(call, context)
    if path is None:
        return []
    docs = _session_files(harness_files, context).find_subdirectory_agents_md(path)
    return [
        (str(directory.resolve()), directory, content) for directory, content in docs
    ]


def agents_md_hook(harness_files: HarnessFilesManager) -> PostToolCallHookHandler:
    """The post-tool hook body. ``injected`` dedups per session.

    ``checked`` holds, per sandboxed session, the directories already looked
    in for a doc (see the module docstring).

    Handlers run on the event loop, so mutation of the per-session sets is
    single-threaded per process.
    """
    injected: dict[str, set[str]] = {}
    checked: dict[str, set[Path]] = {}

    async def handler(
        hook_input: RustPostToolCallHookInput, context: HookContext
    ) -> RustPostToolCallHookResult:
        tool_result = hook_input.tool_result
        call = hook_input.tool_call.call
        if not isinstance(call, RustRuntimeBuiltinToolCall):
            return _passthrough(tool_result)
        session_key = context.session_id or ""
        session_checked = checked.setdefault(session_key, set())
        if context.config.sandbox is not None:
            _forget_changed_lookups(session_checked, call, context)
        if call.name != _READ_FILE_TOOL_NAME:
            return _passthrough(tool_result)
        if not isinstance(tool_result, RustToolSuccessResult):
            return _passthrough(tool_result)
        try:
            keyed = await _keyed_docs(harness_files, call, context, session_checked)
        except Exception:
            return _passthrough(tool_result)
        session_injected = injected.setdefault(session_key, set())
        new_docs = [
            (directory, content)
            for key, directory, content in keyed
            if key not in session_injected
        ]
        if not new_docs:
            return _passthrough(tool_result)
        session_injected.update(key for key, _, _ in keyed)
        sections = "\n\n".join(
            _AGENTS_MD_SECTION.format(directory=directory, content=content.strip())
            for directory, content in new_docs
        )
        text = f"<{VIBE_WARNING_TAG}>\n{sections}\n</{VIBE_WARNING_TAG}>"
        return RustPostToolCallHookResult(
            output=RustPostToolCallOutput(
                tool_result=tool_result.model_copy(
                    update={
                        "content": [
                            *tool_result.content,
                            RustTextContentBlock(text=text),
                        ]
                    }
                )
            )
        )

    return handler


def agents_md_hook_handlers(harness_files: HarnessFilesManager) -> HookHandlers:
    """The Host-global builtin registry payload for ``configure_hook_handlers``."""
    return HookHandlers(
        post_tool_call={AGENTS_MD_HOOK_BINDING_ID: agents_md_hook(harness_files)}
    )


def merge_agents_md_hook(hooks: CompiledHooks) -> CompiledHooks:
    """Bind the AGENTS.md builtin hook point on a session's Core.

    Only the binding: the handler is Host-global (see ``agents_md_hook_handlers``),
    so it never lands in the per-session handler map that marks hooks foreign.

    The order must be unique among all bindings: the Core's config validation
    rejects a duplicate ``order`` outright, and foreign hooks are numbered
    ``0..n-1`` by enumerate, so a constant ``0`` would collide with the first
    user hook and make every session with a hooks.toml file unfetchable.
    Slot the builtin after the last existing binding instead.
    """
    binding = RustHarnessHookBinding(
        id=AGENTS_MD_HOOK_BINDING_ID,
        point="post_tool_call",
        order=max((binding.order for binding in hooks.bindings), default=-1) + 1,
        selector=RustAlwaysHookSelector(),
    )
    return replace(hooks, bindings=(*hooks.bindings, binding))


__all__ = [
    "AGENTS_MD_HOOK_BINDING_ID",
    "agents_md_hook",
    "agents_md_hook_handlers",
    "merge_agents_md_hook",
]
