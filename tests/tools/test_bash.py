from __future__ import annotations

import sys

from pydantic import ValidationError
import pytest

from vibe.core.tools.base import BaseToolState, ToolPermission
from vibe.core.tools.builtins._shell_command_policy import (
    _COMMAND_POLICIES,
    analyze_shell_command_policy,
    has_option_guardrails,
)
from vibe.core.tools.builtins._shell_permission_analysis import (
    analyze_shell_command,
    redirect_writes,
)
import vibe.core.tools.builtins.bash as bash_module
from vibe.core.tools.builtins.bash import (
    Bash,
    BashArgs,
    BashToolConfig,
    CapturedShellResult,
    _get_default_denylist,
    _get_default_denylist_standalone,
    default_read_only_commands,
)
from vibe.core.tools.builtins.experimental_bash import (
    BashLogFileArgs,
    BashLogFileConfig,
    BashOutputArgs,
    BashOutputConfig,
    BashSessionsArgs,
    BashSessionsConfig,
    BashStdinArgs,
    ExperimentalBash,
    ExperimentalBashArgs,
    ExperimentalBashResult,
    ExperimentalBashToolConfig,
)
from vibe.core.tools.models import ApprovedRule, PermissionScope
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionStore,
    RequiredPermission,
)
from vibe.core.utils import is_windows
from vibe.permissions import PathGrantScope, path_grant_pattern


@pytest.fixture
def bash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    return Bash(config_getter=lambda: config, state=BaseToolState())


def _hide_standard_git_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def test_shell_results_keep_a_returncode_alias_for_post_tool_hooks():
    # The verbatim result dump is the `tool_output` payload handed to hooks.
    managed = ExperimentalBashResult(command="x", exit_code=3).model_dump(mode="json")
    assert managed["exit_code"] == 3
    assert managed["returncode"] == 3

    captured = CapturedShellResult(command="x", exit_code=2).model_dump(mode="json")
    assert captured["exit_code"] == 2
    assert captured["returncode"] == 2


def test_managed_shell_returncode_alias_is_zero_while_the_exit_code_is_unknown():
    dumped = ExperimentalBashResult(command="x").model_dump(mode="json")

    assert dumped["exit_code"] is None
    assert dumped["returncode"] == 0


def test_bash_stdin_requires_exactly_one_input_source():
    BashStdinArgs(session_id="s", text="hi")
    BashStdinArgs(session_id="s", control=["ctrl_c"])

    with pytest.raises(ValidationError):
        BashStdinArgs(session_id="s")
    with pytest.raises(ValidationError):
        BashStdinArgs(session_id="s", text="hi", control=["ctrl_c"])


def test_bash_stdin_control_field_rejects_unknown_keys():
    BashStdinArgs(session_id="s", control=["ctrl_c", "enter"])

    with pytest.raises(ValidationError):
        BashStdinArgs.model_validate({"session_id": "s", "control": ["not_a_real_key"]})


def test_bash_byte_limit_models_accept_legacy_max_chars_alias():
    assert (
        BashOutputArgs.model_validate({"session_id": "s", "max_chars": 12}).max_bytes
        == 12
    )
    assert (
        BashSessionsArgs.model_validate({
            "action": "inspect",
            "max_chars": 13,
        }).max_bytes
        == 13
    )
    assert (
        BashLogFileArgs.model_validate({"action": "read", "max_chars": 14}).max_bytes
        == 14
    )


def test_bash_config_models_accept_legacy_max_inline_chars_alias():
    assert (
        ExperimentalBashToolConfig.model_validate({
            "max_inline_chars": 12
        }).max_inline_bytes
        == 12
    )
    assert (
        BashOutputConfig.model_validate({"max_inline_chars": 13}).max_inline_bytes == 13
    )
    assert (
        BashSessionsConfig.model_validate({"max_inline_chars": 14}).max_inline_bytes
        == 14
    )
    assert (
        BashLogFileConfig.model_validate({"max_inline_chars": 15}).max_inline_bytes
        == 15
    )


def test_bash_sessions_schema_distinguishes_kill_from_reset():
    schema = BashSessionsArgs.model_json_schema()

    action_description = schema["properties"]["action"]["description"]
    session_id_description = schema["properties"]["session_id"]["description"]

    assert "terminates exactly that one session" in action_description
    assert "stops every session in this tool family" in action_description
    assert "`kill` affects exactly this one session" in session_id_description


@pytest.mark.parametrize("predicate", ["-exec", "-execdir", "-ok", "-okdir"])
def test_find_execution_predicates_force_ask(predicate: str):
    config = BashToolConfig(permission=ToolPermission.ALWAYS)
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    permission = bash_tool.resolve_permission(
        BashArgs(command=f"find . {predicate} id \\;")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert [required.label for required in permission.required_permissions] == [
        f"find . {predicate} id \\;"
    ]


def test_find_exec_compound_includes_companion_required_permission():
    config = BashToolConfig(permission=ToolPermission.ALWAYS)
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    permission = bash_tool.resolve_permission(
        BashArgs(command='find . -exec id \\; && python3 -c "import os"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    labels = {rp.label for rp in permission.required_permissions}
    assert any("find" in label for label in labels), (
        f"Expected a find-exec RequiredPermission, got {labels}"
    )
    assert any("python3" in label for label in labels), (
        f"Companion command should also require permission, got {labels}"
    )


def test_find_execution_predicate_does_not_override_denylist():
    config = BashToolConfig(denylist=["passwd"])
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    permission = bash_tool.resolve_permission(
        BashArgs(command="find . -exec id \\; && passwd root")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "matches denylist pattern 'passwd'" in (permission.reason or "")


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("wrapper", ["eval", "exec"])
def test_shell_wrappers_preserve_nested_denylist(shell_kind, wrapper):
    """A shell builtin must not downgrade its statically visible command to ASK."""
    if shell_kind == "legacy":
        tool = Bash(
            config_getter=lambda: BashToolConfig(denylist=["denied-marker"]),
            state=BaseToolState(),
        )
        result = tool.resolve_permission(
            BashArgs(command=f"{wrapper} denied-marker harmless")
        )
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(
                denylist=["denied-marker"]
            ),
            state=BaseToolState(),
        )
        result = tool.resolve_permission(
            ExperimentalBashArgs(command=f"{wrapper} denied-marker harmless")
        )

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.NEVER
    assert "matches denylist pattern 'denied-marker'" in (result.reason or "")


def _resolve_default_shell_permission(
    shell_kind: str, command: str
) -> PermissionContext | None:
    if shell_kind == "legacy":
        tool = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        return tool.resolve_permission(BashArgs(command=command))
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )
    return tool.resolve_permission(ExperimentalBashArgs(command=command))


@pytest.mark.parametrize(
    "command_template",
    [
        "cat x#y {outside}",
        "ls x#y {outside}",
        "find . -name x#y -o -delete",
        r"cat x\ #y {outside}",
        r"cat \"x {outside} \"",
        r"cat \'x {outside} \'",
    ],
)
def test_legacy_bash_guardrail_sees_the_tokens_git_bash_runs(
    command_template, tmp_path, monkeypatch
):
    # On Windows, legacy bash only judges commands when Git Bash runs them.
    monkeypatch.setattr(bash_module, "uses_posix_shell", lambda: True)
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside" / "secret.txt"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    permission = _resolve_default_shell_permission(
        "legacy", command_template.format(outside=outside.as_posix())
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "sort -o sorted.txt input.txt",
        "sort -rosorted.txt input.txt",
        "sort --out=sorted.txt input.txt",
        "sort --temporary-directory=. input.txt",
        "sort --compress-program=gzip input.txt",
        "sort --files0-from=input-files.txt",
        "sort --files0-f=input-files.txt",
        "file --files-from=input-files.txt",
        "file --files-f=input-files.txt",
        "file -f input-files.txt",
        "file -C",
        "file --compile",
        "file -z archive.gz",
        "file -Z archive.gz",
        "du --files0-from=input-files.txt",
        "du --files0-f=input-files.txt",
        "wc --files0-from=input-files.txt",
        "wc --files0-f=input-files.txt",
        "find . -delete",
        "find . -fprint matches.txt",
        "find -files0-from input-files.txt",
        r"find . -exec echo {} \;",
        "md5sum --check checksums.txt",
        "sha1sum -c checksums.txt",
        "sha256sum --check checksums.txt",
        "shasum -c checksums.txt",
        "less -o less.log input.txt",
        "less -Noless.log input.txt",
        "less --LOG-FILE=less.log input.txt",
        "tree -o tree.txt .",
        "tree -aotree.txt .",
        "tree -Xo ignored tree.txt .",
        "tree -Ho ignored tree.txt .",
        "tree -Io ignored tree.txt .",
        "tree -Lo 2 tree.txt .",
        "tree -Po ignored tree.txt .",
        "tree -To ignored tree.txt .",
        "uniq input.txt output.txt",
        "uniq input.txt +output.txt",
        "uniq -f 1 input.txt output.txt",
        "git diff --output=diff.txt",
        "git diff --out=diff.txt",
        "git log --output=log.txt",
        "git diff --ext-diff",
        "git log --remerge-diff -1",
        "git log -p --diff-merges=remerge",
        "git log -p --diff-merges=r",
        "date -s 2020-01-01",
        "date -us 2020-01-01",
        "date --set=2020-01-01",
        "date --se=2020-01-01",
        "date 010112002026",
        "date -f %Y 2026",
    ],
)
def test_side_effecting_allowlisted_options_require_approval(shell_kind, command):
    permission = _resolve_default_shell_permission(shell_kind, command)

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("pager", ["less", "more"])
@pytest.mark.parametrize(
    "arguments",
    [
        "--lesskey-src=keys",
        "--lesskey-src keys",
        "--lesskey-sr=keys",
        "--lesskey-sr keys",
        "--lesskey-context=keys",
        "--lesskey-context keys",
        "--lesskey-contex=keys",  # typos:disable-line
        "--lesskey-contex keys",  # typos:disable-line
        "--lesskey-content=keys",
        "--lesskey-content keys",
        "--lesskey-conten=keys",  # typos:disable-line
        "--lesskey-conten keys",  # typos:disable-line
        "--lesskey-con=keys",
        "--lesskey-con keys",
        "--lesskey-file=keys",
        "--lesskey-file keys",
        "--lesskey-fi=keys",
        "--lesskey-fi keys",
        "--Lesskey-src=keys",
        "--LESSKEY-CONTEXT keys",
        "--Lesskey-Fi=keys",
        "--+LESSKEY-SRC=keys",
        "--+lesskey-src=keys",
        "--+lesskey-src keys",
        "--+lesskey-content=keys",
        "--+lesskey-content keys",
        "--+lesskey-file=keys",
        "--+lesskey-file keys",
        "--+lesskey-sr=keys",
        "--+lesskey-conten keys",  # typos:disable-line
        "--+lesskey-fi=keys",
        "-kkeys",
        "-k keys",
        "-Nkkeys",
        "-Nk keys",
        "-+kkeys",
        "-+k keys",
    ],
)
def test_lesskey_configuration_options_require_approval(shell_kind, pager, arguments):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{pager} {arguments} input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("pager", ["less", "more"])
@pytest.mark.parametrize(
    "non_consuming_option",
    [
        "--header",
        "--intr",
        "--match-shift",
        "--modelines",
        "--search-options",
        "--line-num-width",
        "--status-col-width",
        "--wheel-lines",
        "--ma",
        "--p",
        "--s",
        "--w",
    ],
)
@pytest.mark.parametrize("lesskey_option", ["-kkeys", "-k keys", "--lesskey-src=keys"])
def test_non_consuming_less_options_do_not_hide_lesskey_options(
    shell_kind, pager, non_consuming_option, lesskey_option
):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{pager} {non_consuming_option} {lesskey_option} input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("pager", ["less", "more"])
@pytest.mark.parametrize("numeric_option", ["#", "b", "h", "j", "x", "y", "z"])
@pytest.mark.parametrize(
    "suffix",
    ["-kkeys", "kkeys", "-olog.txt", "tlabel", "--lesskey-src=keys", "+!harmless"],
)
def test_short_numeric_less_options_do_not_hide_following_options(
    shell_kind, pager, numeric_option, suffix
):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{pager} -{numeric_option}8{suffix} input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("pager", ["less", "more"])
@pytest.mark.parametrize(
    "numeric_option",
    [
        "--buffers",
        "--jump-target",
        "--max-back-scroll",
        "--max-forw-scroll",
        "--shift",
        "--tab",
        "--tabs",
        "--window",
        "--header",
        "--line-num-width",
        "--match-shift",
        "--modelines",
        "--status-col-width",
        "--wheel-lines",
    ],
)
@pytest.mark.parametrize(
    "suffix",
    ["-kkeys", "kkeys", "-olog.txt", "tlabel", "--lesskey-src=keys", "+!harmless"],
)
def test_long_numeric_less_options_do_not_hide_following_options(
    shell_kind, pager, numeric_option, suffix
):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{pager} {numeric_option}=8{suffix} input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("pager", ["less", "more"])
@pytest.mark.parametrize(
    "arguments",
    [
        r"'-Pprompt$kkeys'",
        r"'--prompt=prompt$kkeys'",
        r"'-Dsr$kkeys'",
        r"'--color=sr$kkeys'",
        "'-\"ab$kkeys'",
        r"'--quotes=ab$kkeys'",
        r"'-Pprompt$+!harmless'",
        r"'--prompt=prompt$--lesskey-src=keys'",
        r"'--emouse=mask$-kkeys'",
        r"'--end-prompt=prompt$-kkeys'",
        r"'--autosave=history$-kkeys'",
    ],
)
def test_attached_less_string_terminator_cannot_hide_lesskey_options(
    shell_kind, pager, arguments
):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{pager} {arguments} input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("pager", ["less", "more"])
@pytest.mark.parametrize(
    "arguments",
    [
        "'--lesskey-src keys'",
        "'+/needle$-kkeys'",
        "'-Dsr -kkeys'",
        "'--color=sr -kkeys'",
        "'-b+1!harmless'",
        "-8+!harmless",
        "-8+v",
        "-n8+!harmless",
        "-N+!harmless",
        "'-$+!harmless'",
        "'-Pprompt$$+!harmless'",
        "-N--cmd=harmless",
    ],
)
def test_less_embedded_option_resumption_requires_approval(
    shell_kind, pager, arguments
):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{pager} {arguments} input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("pager", ["less", "more"])
@pytest.mark.parametrize(
    "arguments",
    [
        "'safe -- -kkeys'",
        "'safe -- --lesskey-src=keys'",
        "'-Pprompt text$kkeys'",
        "'--prompt=prompt text$--lesskey-src=keys'",
    ],
)
def test_quoted_less_arguments_cannot_hide_lesskey_options(
    shell_kind, pager, arguments
):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{pager} {arguments} input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("pager", ["less", "more"])
@pytest.mark.parametrize(
    "arguments",
    [
        "+!harmless",
        "+#harmless",
        "+|harmless",
        "+sharmless",
        "+v",
        "+:e harmless.txt",
        "--cmd=harmless",
        "--cmd harmless",
        "--cm=harmless",
        "--cm harmless",
        "--+cmd=harmless",
        "--+cmd harmless",
        "--+cm=harmless",
        "-t harmless",
        "--tag=harmless",
        "--tag harmless",
        "-Tharmless.tags",
        "-T harmless.tags",
        "--tag-file=harmless.tags",
        "--tag-file harmless.tags",
    ],
)
def test_less_startup_commands_and_tag_options_require_approval(
    shell_kind, pager, arguments
):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{pager} {arguments} input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("pager", ["less", "more"])
@pytest.mark.parametrize("prefix", ["+/", "++/"])
@pytest.mark.parametrize(
    "control",
    ["\x07", "\x08", "\x1b", "\x7f", "\r", "\n"],
    ids=["bell", "backspace", "escape", "delete", "carriage-return", "newline"],
)
def test_less_startup_search_control_characters_require_approval(
    shell_kind, pager, prefix, control
):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{pager} '{prefix}needle{control}v' input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "assignment",
    [
        "LESSOPEN=harmless",
        "LESSCLOSE=harmless",
        "LESSKEYIN=harmless.lesskey",
        "LESSKEY_CONTENT=harmless",
    ],
)
def test_less_environment_assignments_require_approval(shell_kind, assignment):
    permission = _resolve_default_shell_permission(
        shell_kind, f"{assignment} less input.txt"
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_managed_less_environment_override_requires_approval():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    permission = tool.resolve_permission(
        ExperimentalBashArgs(command="less input.txt", env={"LESSOPEN": "harmless"})
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert [required.label for required in permission.required_permissions] == [
        "custom environment (LESSOPEN)"
    ]


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "less input.txt && less -kkeys input.txt",
        "exec less -kkeys input.txt",
        "eval 'less --lesskey-src keys input.txt'",
    ],
)
def test_shell_wrappers_do_not_hide_lesskey_options(shell_kind, command):
    permission = _resolve_default_shell_permission(shell_kind, command)

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "sort -r input.txt",
        "md5sum input.txt",
        "shasum -a 256 input.txt",
        "find . -name '*.py'",
        "less -N input.txt",
        "less -Pkey input.txt",
        "less -P -k input.txt",
        "less --prompt -k input.txt",
        "less -# -k input.txt",
        "less -x8Pprompt-k input.txt",
        "less --tabs=8Pprompt-k input.txt",
        "less +G input.txt",
        "less +42 input.txt",
        "less +/needle input.txt",
        "less -- --lesskey-src=ordinary-filename",
        "less -- -kordinary-filename",
        "less -- --+lesskey-content=ordinary-filename",
        "less -- +G",
        "more -N input.txt",
        "more +10 input.txt",
        "more +/needle input.txt",
        "more -- --lesskey-src=ordinary-filename",
        "more -- -kordinary-filename",
        "more -- --+lesskey-content=ordinary-filename",
        "tree -L 2 .",
        "sort -- --output=ordinary-filename",
        "date -d yesterday",
        "date -Iseconds",
        "date -j -f %Y 2026",
        "date -- -s",
    ],
)
def test_benign_allowlisted_options_remain_allowed(shell_kind, command):
    permission = _resolve_default_shell_permission(shell_kind, command)

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


@pytest.mark.skipif(is_windows(), reason="outside-dir permissions are POSIX-only")
@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command_template",
    [
        "tree {outside}",
        "git diff --no-index {outside} other.txt",
        "sort --random-source={outside} input.txt",
        "sort --random={outside} input.txt",
        "git diff -O{outside}",
        "git diff --pathspec-from-file={outside}",
        "git log -O{outside}",
        "git log --pathspec-from-file={outside}",
        "git status --pathspec-from-file={outside}",
        "tree --gitfile={outside} .",
    ],
)
def test_allowlisted_option_paths_outside_workspace_require_approval(
    shell_kind, command_template, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside" / "data.txt"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    permission = _resolve_default_shell_permission(
        shell_kind, command_template.format(outside=outside)
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.invocation_pattern == str(outside.resolve())
        for required in permission.required_permissions
    )


@pytest.mark.skipif(is_windows(), reason="outside-dir permissions are POSIX-only")
@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command_template",
    [
        "grep --file={outside} input.txt",
        "grep --fil={outside} input.txt",
        "grep -if{outside} input.txt",
        "grep --exclude-from={outside} input.txt",
        "file --files-from={outside}",
        "file --files-f={outside}",
        "file -f{outside}",
        "file --magic-file={outside} input.txt",
        "file --magic-f={outside} input.txt",
        "file -m{outside} input.txt",
        "file -m{outside}:magic.mgc input.txt",
        "du --files0-from={outside}",
        "du --files0-f={outside}",
        "du --exclude-from={outside}",
        "du -X{outside}",
        "wc --files0-from={outside}",
        "wc --files0-f={outside}",
        "date --file={outside}",
        "date --fil={outside}",
        "date -uf{outside}",
        "diff --from-file={outside} input.txt",
        "diff --to={outside} input.txt",
        "diff --exclude-from={outside} input.txt",
        "diff -X{outside} input.txt",
    ],
)
def test_read_only_option_paths_outside_workspace_require_approval(
    shell_kind, command_template, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside" / "data.txt"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    permission = _resolve_default_shell_permission(
        shell_kind, command_template.format(outside=outside)
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.invocation_pattern == str(outside.resolve())
        for required in permission.required_permissions
    )


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "grep --file=patterns.txt input.txt",
        "grep --exclude-from=patterns.txt input.txt",
        "file --magic-file=magic.mgc input.txt",
        "du -Xpatterns.txt .",
        "date -f dates.txt",
        "diff --from-file=base.txt input.txt",
        "diff -Xpatterns.txt input.txt",
        "tree --gitfile=.gitignore .",
    ],
)
def test_read_only_option_paths_inside_workspace_remain_allowed(shell_kind, command):
    permission = _resolve_default_shell_permission(shell_kind, command)

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


@pytest.mark.skipif(is_windows(), reason="outside-dir permissions are POSIX-only")
@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("target_exists", [False, True])
def test_shell_path_scope_root_does_not_widen_file_targets(
    shell_kind, target_exists, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    target = outside / "target.txt"
    workdir.mkdir()
    outside.mkdir()
    if target_exists:
        target.write_text("content", encoding="utf-8")
    monkeypatch.chdir(workdir)

    permission = _resolve_default_shell_permission(shell_kind, f"cat {target}")

    assert isinstance(permission, PermissionContext)
    [required] = [
        item
        for item in permission.required_permissions
        if item.scope is PermissionScope.OUTSIDE_DIRECTORY
    ]
    assert required.path_scope_root is None


@pytest.mark.skipif(is_windows(), reason="outside-dir permissions are POSIX-only")
@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
def test_shell_path_scope_root_does_not_widen_unreadable_targets(
    shell_kind, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    target = outside / "target"
    workdir.mkdir()
    outside.mkdir()
    target.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setattr("vibe.core.tools.utils.os.access", lambda *_args: False)

    permission = _resolve_default_shell_permission(shell_kind, f"cat {target}")

    assert isinstance(permission, PermissionContext)
    [required] = [
        item
        for item in permission.required_permissions
        if item.scope is PermissionScope.OUTSIDE_DIRECTORY
    ]
    assert required.path_scope_root is None


@pytest.mark.skipif(is_windows(), reason="outside-dir permissions are POSIX-only")
@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
def test_shell_path_scope_root_identifies_existing_directories(
    shell_kind, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    target = tmp_path / "outside"
    workdir.mkdir()
    target.mkdir()
    monkeypatch.chdir(workdir)

    permission = _resolve_default_shell_permission(shell_kind, f"ls {target}")

    assert isinstance(permission, PermissionContext)
    [required] = [
        item
        for item in permission.required_permissions
        if item.scope is PermissionScope.OUTSIDE_DIRECTORY
    ]
    assert required.path_scope_root == str(target)


@pytest.mark.skipif(is_windows(), reason="uses POSIX symlinks")
@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
def test_recursive_folder_grant_does_not_cover_symlink_target(
    shell_kind, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    approved = tmp_path / "approved"
    unapproved = tmp_path / "unapproved"
    workdir.mkdir()
    approved.mkdir()
    unapproved.mkdir()
    secret = unapproved / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (approved / "link").symlink_to(unapproved, target_is_directory=True)
    monkeypatch.chdir(workdir)
    grant = path_grant_pattern(
        str(approved.resolve()), PathGrantScope.DIRECTORY_RECURSIVE
    )

    if shell_kind == "legacy":
        config = BashToolConfig()
        config.allowlist.append(grant)
        tool = Bash(config_getter=lambda: config, state=BaseToolState())
        permission = tool.resolve_permission(
            BashArgs(command=f"cat {approved / 'link' / 'secret.txt'}")
        )
    else:
        config = ExperimentalBashToolConfig()
        config.allowlist.append(grant)
        tool = ExperimentalBash(config_getter=lambda: config, state=BaseToolState())
        permission = tool.resolve_permission(
            ExperimentalBashArgs(command=f"cat {approved / 'link' / 'secret.txt'}")
        )

    assert isinstance(permission, PermissionContext)
    outside = [
        item
        for item in permission.required_permissions
        if item.scope is PermissionScope.OUTSIDE_DIRECTORY
    ]
    assert [item.invocation_pattern for item in outside] == [str(secret.resolve())]


@pytest.mark.skipif(is_windows(), reason="outside-dir permissions are POSIX-only")
def test_legacy_bash_quoted_outside_path_requires_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    bash_tool = Bash(
        config_getter=lambda: BashToolConfig(permission=ToolPermission.ASK),
        state=BaseToolState(),
    )

    permission = bash_tool.resolve_permission(BashArgs(command=f'cat "{outside}"'))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.invocation_pattern == str(outside.resolve())
        for required in permission.required_permissions
    )


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
def test_experimental_bash_quoted_outside_path_requires_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    bash_tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(permission=ToolPermission.ASK),
        state=BaseToolState(),
    )

    permission = bash_tool.resolve_permission(
        ExperimentalBashArgs(command=f'cat "{outside}"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.invocation_pattern == str(outside.resolve())
        for required in permission.required_permissions
    )


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.parametrize("command", ["grep root", "find", "od -c"])
def test_experimental_bash_readers_require_approval_for_outside_paths(
    command, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")
    monkeypatch.chdir(workdir)
    bash_tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    permission = bash_tool.resolve_permission(
        ExperimentalBashArgs(command=f"{command} {outside_file}")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_resolve_permission():
    config = BashToolConfig(allowlist=["echo", "pwd"], denylist=["rm"])
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    allowlisted = bash_tool.resolve_permission(BashArgs(command="echo hi"))
    denylisted = bash_tool.resolve_permission(BashArgs(command="rm -rf /tmp"))
    mixed = bash_tool.resolve_permission(BashArgs(command="pwd && whoami"))
    empty = bash_tool.resolve_permission(BashArgs(command=""))

    assert isinstance(allowlisted, PermissionContext)
    assert allowlisted.permission is ToolPermission.ALWAYS
    assert isinstance(denylisted, PermissionContext)
    assert denylisted.permission is ToolPermission.NEVER
    assert isinstance(mixed, PermissionContext)
    assert mixed.permission is ToolPermission.ASK
    assert any(rp.label == "whoami *" for rp in mixed.required_permissions)
    assert empty is None


class TestResolvePermissionWindowsSyntax:
    """Verify allowlist/denylist works with Windows-style commands."""

    def _make_bash(self, **kwargs) -> Bash:
        config = BashToolConfig(**kwargs)
        return Bash(config_getter=lambda: config, state=BaseToolState())

    def test_dir_with_windows_flags_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["dir"])
        result = bash_tool.resolve_permission(BashArgs(command="dir /s /b"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_type_command_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["type"])
        result = bash_tool.resolve_permission(BashArgs(command="type file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_findstr_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["findstr"])
        result = bash_tool.resolve_permission(
            BashArgs(command="findstr /s pattern *.txt")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_ver_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["ver"])
        result = bash_tool.resolve_permission(BashArgs(command="ver"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_where_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["where"])
        result = bash_tool.resolve_permission(BashArgs(command="where python"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_cmd_k_denylisted(self):
        bash_tool = self._make_bash(denylist=["cmd /k"])
        result = bash_tool.resolve_permission(BashArgs(command="cmd /k something"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_powershell_noexit_denylisted(self):
        bash_tool = self._make_bash(denylist=["powershell -NoExit"])
        result = bash_tool.resolve_permission(BashArgs(command="powershell -NoExit"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_notepad_denylisted(self):
        bash_tool = self._make_bash(denylist=["notepad"])
        result = bash_tool.resolve_permission(BashArgs(command="notepad file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_cmd_standalone_denylisted(self):
        bash_tool = self._make_bash(denylist_standalone=["cmd"])
        result = bash_tool.resolve_permission(BashArgs(command="cmd"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_powershell_standalone_denylisted(self):
        bash_tool = self._make_bash(denylist_standalone=["powershell"])
        result = bash_tool.resolve_permission(BashArgs(command="powershell"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_powershell_cmdlet_asks(self):
        bash_tool = self._make_bash(allowlist=["dir", "echo"])
        result = bash_tool.resolve_permission(BashArgs(command="Get-ChildItem -Path ."))
        assert isinstance(result, PermissionContext)
        assert result.permission == ToolPermission.ASK

    def test_mixed_allowed_and_unknown_asks(self):
        bash_tool = self._make_bash(allowlist=["git status"])
        result = bash_tool.resolve_permission(
            BashArgs(command="git status && npm install")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission == ToolPermission.ASK

    def test_chained_windows_commands_all_allowed(self):
        bash_tool = self._make_bash(allowlist=["dir", "echo"])
        result = bash_tool.resolve_permission(BashArgs(command="dir /s && echo done"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_chained_commands_one_denied(self):
        bash_tool = self._make_bash(allowlist=["dir"], denylist=["rm"])
        result = bash_tool.resolve_permission(BashArgs(command="dir /s && rm -rf /"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_piped_windows_commands(self):
        bash_tool = self._make_bash(allowlist=["findstr", "type"])
        result = bash_tool.resolve_permission(
            BashArgs(command="type file.txt | findstr pattern")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS


class TestDenylistWordBoundary:
    """Verify denylist matches whole command names, not prefixes."""

    def _make_bash(self, **kwargs) -> Bash:
        config = BashToolConfig(**kwargs)
        return Bash(config_getter=lambda: config, state=BaseToolState())

    def test_vi_blocks_vi_exact(self):
        bash_tool = self._make_bash(denylist=["vi"])
        result = bash_tool.resolve_permission(BashArgs(command="vi"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_vi_blocks_vi_with_args(self):
        bash_tool = self._make_bash(denylist=["vi"])
        result = bash_tool.resolve_permission(BashArgs(command="vi file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_vi_does_not_block_vibe(self):
        bash_tool = self._make_bash(denylist=["vi"])
        result = bash_tool.resolve_permission(BashArgs(command="vibe -p hello"))
        assert result is None or result.permission is not ToolPermission.NEVER

    def test_multiword_pattern_still_works(self):
        bash_tool = self._make_bash(denylist=["bash -i"])
        result = bash_tool.resolve_permission(BashArgs(command="bash -i"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_multiword_pattern_with_trailing_args(self):
        bash_tool = self._make_bash(denylist=["bash -i"])
        result = bash_tool.resolve_permission(BashArgs(command="bash -i extra"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_multiword_pattern_does_not_match_partial(self):
        bash_tool = self._make_bash(denylist=["bash -i"])
        result = bash_tool.resolve_permission(BashArgs(command="bash -init"))
        assert result is None or result.permission is not ToolPermission.NEVER

    def test_deny_reason_is_set(self):
        bash_tool = self._make_bash(denylist=["vim"])
        result = bash_tool.resolve_permission(BashArgs(command="vim file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.reason is not None
        assert "vim" in result.reason

    def test_standalone_deny_reason_is_set(self):
        bash_tool = self._make_bash(denylist_standalone=["python"])
        result = bash_tool.resolve_permission(BashArgs(command="python"))
        assert isinstance(result, PermissionContext)
        assert result.reason is not None
        assert result.permission is ToolPermission.NEVER
        assert "python" in result.reason
        assert "standalone" in result.reason

    def test_allowlist_does_not_match_prefix(self):
        bash_tool = self._make_bash(allowlist=["cat"])
        result = bash_tool.resolve_permission(BashArgs(command="catalog"))
        assert result is not None and result.permission is not ToolPermission.ALWAYS


def test_default_allowlist_includes_read_only_commands():
    """Test that common read-only commands are in the default allowlist."""
    from vibe.core.tools.builtins.bash import _get_default_allowlist

    allowlist = _get_default_allowlist()

    # Read-only commands that should be in the default allowlist
    read_only_commands = [
        "grep",
        "cut",
        "sort",
        "tr",
        "uniq",
        "basename",
        "comm",
        "date",
        "diff",
        "dirname",
        "du",
        "fmt",
        "fold",
        "join",
        "less",
        "md5sum",
        "more",
        "nl",
        "od",
        "paste",
        "readlink",
        "sha1sum",
        "sha256sum",
        "shasum",
        "stat",
        "sum",
        "tac",
        "which",
    ]

    for cmd in read_only_commands:
        assert cmd in allowlist, (
            f"Read-only command '{cmd}' should be in default allowlist"
        )


def test_new_read_only_commands_are_allowlisted():
    """Test that newly added read-only commands are automatically allowed."""
    config = BashToolConfig()  # Use default config
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    # Test that newly added read-only commands are allowed by default
    test_commands = [
        "grep pattern file.txt",
        "cut -d',' -f1 file.csv",
        "sort file.txt",
        "tr 'a' 'b'",
        "uniq file.txt",
        "basename file.txt",
        "comm file1.txt file2.txt",
        "date",
        "diff file1.txt file2.txt",
        "dirname file.txt",
        "du -sh .",
        "fmt file.txt",
        "fold -w 80 file.txt",
        "join -t',' file1.csv file2.csv",
        "less file.txt",
        "md5sum file.txt",
        "more file.txt",
        "nl file.txt",
        "od -c file.bin",
        "paste file1.txt file2.txt",
        "readlink -f link.txt",
        "sha1sum file.txt",
        "sha256sum file.txt",
        "shasum file.txt",
        "stat file.txt",
        "sum file.txt",
        "tac file.txt",
        "which python",
    ]

    for cmd in test_commands:
        permission = bash_tool.resolve_permission(BashArgs(command=cmd))
        assert isinstance(permission, PermissionContext), (
            f"Permission should be PermissionContext for '{cmd}'"
        )
        assert permission.permission is ToolPermission.ALWAYS, (
            f"Command '{cmd}' should be always allowed"
        )


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        r"find . $'-exec' echo harmless {} \;",
        r"find . $'-ex'c echo harmless {} \;",
        r'find . "$(basename ./-exe)c" echo harmless {} \;',
        "echo $( {/bin/bash,-c,id} ) $( (){ /bin/bash -c id } )",
        "GIT_PAGER=cat git diff",
        "PATH=/tmp; git status",
        "PATH=/tmp && git status",
        "(PATH=/tmp; git status)",
        "{ PATH=/tmp; git status; }",
        "for value in 1; do PATH=/tmp; git status; done",
        "FOO=bar",
        'cat "$HOME/.ssh/id_rsa"',
        "echo ${VALUE:-harmless}",
        "echo {harmless,safe}",
        "echo {1..3}",
        "echo $((1 + 2))",
        "cat =sh",
        "grep --file==sh pattern README.md",
        "cat ~-/secret",
        "cat ~+2/secret",
        "cat ~vault/secret",
        "cat ***/*",
        "cat =(printf harmless)",
        "echo ${(e)payload}",
        "echo *(e:'true':)",
        "[[ -r /etc/passwd ]] && echo readable",
        "[ -r /etc/passwd ] && echo readable",
        "for value in harmless; do echo harmless; done",
        "for ((i = 0; i < 1; i++)); do echo harmless; done",
        "while echo harmless; do echo harmless; done",
        "if echo harmless; then echo harmless; fi",
        "case harmless in harmless) echo harmless;; esac",
        "(echo harmless)",
        "{ echo harmless; }",
        "harmless() { echo harmless; }",
        "echo harmless &",
        "echo harmless\\\n#;echo hidden",
    ],
    ids=[
        "ansi-c-string",
        "nested-ansi-c-string",
        "command-substitution",
        "parse-error",
        "environment-assignment",
        "standalone-assignment",
        "and-list-assignment",
        "subshell-assignment",
        "group-assignment",
        "loop-assignment",
        "assignment-only",
        "variable-expansion",
        "parameter-default-expansion",
        "brace-expansion",
        "brace-range-expansion",
        "arithmetic-expansion",
        "zsh-equals-expansion",
        "zsh-magic-equals-expansion",
        "zsh-directory-stack-expansion",
        "zsh-directory-stack-index-expansion",
        "zsh-named-directory-expansion",
        "zsh-symlink-following-glob",
        "zsh-temp-file-substitution",
        "zsh-parameter-evaluation",
        "zsh-executable-glob-qualifier",
        "double-bracket-test-command",
        "single-bracket-test-command",
        "for-loop",
        "c-style-for-loop",
        "while-loop",
        "conditional",
        "case-conditional",
        "subshell",
        "group",
        "function-definition",
        "background",
        "line-continuation",
    ],
)
def test_shell_permission_analysis_fails_closed(shell_kind, command):
    """Permission resolution must reject syntax not modeled for auto-approval."""
    if shell_kind == "legacy":
        tool = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = tool.resolve_permission(BashArgs(command=command))
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
        )
        result = tool.resolve_permission(ExperimentalBashArgs(command=command))

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "echo harmless > output.txt",
        "echo harmless >> output.txt",
        "cat < input.txt",
        "echo harmless &> output.txt",
        "cat <<'EOF'\nharmless\nEOF",
        "cat <<< harmless",
        "> output.txt",
        "{ echo harmless; } > output.txt",
        'echo "$(echo harmless > output.txt)"',
        "cat <(echo harmless)",
        "echo harmless > >(cat)",
    ],
    ids=[
        "output",
        "append",
        "input",
        "combined-output",
        "heredoc",
        "here-string",
        "redirect-only",
        "compound",
        "nested",
        "process-substitution-input",
        "process-substitution-output",
    ],
)
def test_shell_redirections_require_approval(shell_kind, command):
    """Redirection and process substitution must never be auto-approved."""
    if shell_kind == "legacy":
        tool = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = tool.resolve_permission(BashArgs(command=command))
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
        )
        result = tool.resolve_permission(ExperimentalBashArgs(command=command))

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "echo harmless 2>&1",
        "echo harmless >/dev/null",
        "echo harmless 2>/dev/null",
        "echo harmless &>/dev/null",
        "echo harmless >/dev/null 2>&1",
        "ls -la ./x.md 2>&1 | head -1",
    ],
    ids=[
        "fd-duplication",
        "discard-stdout",
        "discard-stderr",
        "discard-both",
        "discard-and-duplicate",
        "pipeline-with-duplication",
    ],
)
def test_redirections_that_cannot_reach_a_file_are_auto_approved(shell_kind, command):
    """A descriptor duplication or a write to /dev/null creates nothing.

    Approval exists to gate a command writing or reading a path the user did not
    authorise. ``2>&1`` renumbers a descriptor and ``/dev/null`` is discarded by the
    kernel, so charging a prompt to the ordinary "run it and drop the noise" shape
    buys no safety and trains the user to click through prompts.
    """
    result = _resolve_default_shell_permission(shell_kind, command)

    assert result is None or result.permission is ToolPermission.ALWAYS


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        # A benign redirect beside a real one must not launder it.
        "echo harmless > output.txt 2>&1",
        "echo harmless >/dev/null 2>err.txt",
        # ``>&word`` is "both streams into that file", not a duplication.
        "echo harmless >&output.txt",
        # Only the exact device is discarded.
        "echo harmless >/dev/nullx",
        "echo harmless >/dev/stdout",
    ],
)
def test_a_benign_redirect_does_not_clear_the_rest_of_the_command(shell_kind, command):
    """Each redirect is judged on its own, so one discard cannot cover a write."""
    result = _resolve_default_shell_permission(shell_kind, command)

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    ["cat <(printf harmless > marker.txt)", "cat <<EOF\n$(printf harmless)\nEOF"],
    ids=["process-substitution-with-nested-redirect", "unquoted-heredoc-substitution"],
)
def test_nested_dynamic_redirections_require_approval(shell_kind, command):
    """Nested redirects and substitutions must remain visible to permission policy."""
    if shell_kind == "legacy":
        tool = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = tool.resolve_permission(BashArgs(command=command))
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
        )
        result = tool.resolve_permission(ExperimentalBashArgs(command=command))

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("command", ["git diff", "git log", "git status"])
def test_git_readers_remain_allowed_for_an_ordinary_repository(
    shell_kind, command, tmp_path, monkeypatch
):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
    monkeypatch.chdir(tmp_path)
    if shell_kind == "legacy":
        tool = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = tool.resolve_permission(BashArgs(command=command))
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
        )
        result = tool.resolve_permission(ExperimentalBashArgs(command=command))

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ALWAYS


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    ("command", "config"),
    [
        ("git status", "[core]\n\tfsmonitor = ./monitor\n"),
        ("git status", '[filter "unsafe"]\n\tclean = ./clean\n'),
        ("git diff", '[filter "unsafe"]\n\tprocess = ./filter\n'),
        ("git diff", "[diff]\n\texternal = ./external-diff\n"),
        ("git diff", "[diff.unsafe]\n\ttextconv = ./textconv\n"),
        ("git log -p", "[diff.unsafe]\n\ttextconv = ./textconv\n"),
        ("git status -v", "[diff]\n\texternal = ./external-diff\n"),
        ("git diff", "[core]\n\tpager = ./pager\n"),
        ("git log", "[pager]\n\tlog = ./pager\n"),
        ("git status", "[pager]\n\tstatus = ./pager\n"),
        ("git status", "[include]\n\tpath = ./included-config\n"),
        ("git log", "[gpg]\n\tprogram = ./fake-gpg\n"),
        ("git log", '[gpg "ssh"]\n\tprogram = ./fake-gpg\n'),
        ("git log", '[merge "unsafe"]\n\tdriver = ./merge-driver\n'),
    ],
    ids=[
        "fsmonitor",
        "clean-filter",
        "process-filter",
        "external-diff",
        "old-style-textconv",
        "log-textconv",
        "status-external-diff",
        "core-pager",
        "log-pager",
        "status-pager",
        "include",
        "signature-program",
        "ssh-signature-program",
        "merge-driver",
    ],
)
def test_git_readers_require_approval_for_executable_repository_config(
    shell_kind, command, config, tmp_path, monkeypatch
):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(config)
    monkeypatch.chdir(tmp_path)
    if shell_kind == "legacy":
        tool = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = tool.resolve_permission(BashArgs(command=command))
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
        )
        result = tool.resolve_permission(ExperimentalBashArgs(command=command))

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
def test_git_reader_approval_is_scoped_to_the_repository(
    shell_kind, tmp_path, monkeypatch
):
    repositories = [tmp_path / "first", tmp_path / "second"]
    for repository in repositories:
        (repository / ".git").mkdir(parents=True)
        (repository / ".git" / "config").write_text("[core]\n\tfsmonitor = ./monitor\n")

    monkeypatch.chdir(repositories[0])
    first = _resolve_default_shell_permission(shell_kind, "git status")
    assert isinstance(first, PermissionContext)
    assert first.permission is ToolPermission.ASK
    assert len(first.required_permissions) == 1
    granted = first.required_permissions[0]
    store = PermissionStore()
    store.add_rule(
        ApprovedRule(
            tool_name="bash",
            scope=granted.scope,
            session_pattern=granted.session_pattern,
        )
    )

    same = _resolve_default_shell_permission(shell_kind, "git status")
    assert isinstance(same, PermissionContext)
    assert store.covers("bash", same.required_permissions[0])

    monkeypatch.chdir(repositories[1])
    second = _resolve_default_shell_permission(shell_kind, "git status")
    assert isinstance(second, PermissionContext)
    assert second.permission is ToolPermission.ASK
    assert not store.covers("bash", second.required_permissions[0])
    assert granted.session_pattern != second.required_permissions[0].session_pattern


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("config", ["", "[core]\n\tfsmonitor = ./monitor\n"])
def test_git_reader_inspects_repository_reached_by_cd(
    shell_kind, config, tmp_path, monkeypatch
):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
    nested = tmp_path / "nested"
    (nested / ".git").mkdir(parents=True)
    (nested / ".git" / "config").write_text(config)
    monkeypatch.chdir(tmp_path)
    if shell_kind == "legacy":
        tool = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = tool.resolve_permission(BashArgs(command="cd nested && git status"))
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
        )
        result = tool.resolve_permission(
            ExperimentalBashArgs(command="cd nested && git status")
        )

    assert isinstance(result, PermissionContext)
    expected = ToolPermission.ASK if config else ToolPermission.ALWAYS
    assert result.permission is expected


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
def test_repeated_git_reader_inspects_every_repository_reached_by_cd(
    shell_kind, tmp_path, monkeypatch
):
    clean = tmp_path / "clean"
    evil = clean / "evil"
    for repository in (tmp_path, clean, evil):
        (repository / ".git").mkdir(parents=True)
        (repository / ".git" / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
        )
    (evil / ".git" / "config").write_text("[diff]\n\texternal = ./evil-diff\n")
    monkeypatch.chdir(tmp_path)

    result = _resolve_default_shell_permission(
        shell_kind, "cd clean && git diff && cd evil && git diff"
    )

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    git_permissions = [
        permission
        for permission in result.required_permissions
        if permission.label == "git diff"
    ]
    assert len(git_permissions) == 1
    assert str(evil.resolve()) in git_permissions[0].invocation_pattern


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command", ["pushd evil && git diff", "pushd evil && popd && git diff"]
)
def test_git_reader_tracks_directory_stack_builtins(
    shell_kind, command, tmp_path, monkeypatch
):
    evil = tmp_path / "evil"
    for repository in (tmp_path, evil):
        (repository / ".git").mkdir(parents=True)
        (repository / ".git" / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
        )
    (evil / ".git" / "config").write_text("[diff]\n\texternal = ./evil-diff\n")
    monkeypatch.chdir(tmp_path)
    if shell_kind == "legacy":
        tool = Bash(
            config_getter=lambda: BashToolConfig(
                allowlist=["pushd", "popd", "git diff"]
            ),
            state=BaseToolState(),
        )
        result = tool.resolve_permission(BashArgs(command=command))
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(
                allowlist=["pushd", "popd", "git diff"]
            ),
            state=BaseToolState(),
        )
        result = tool.resolve_permission(ExperimentalBashArgs(command=command))

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
def test_git_reader_requires_approval_after_globbed_cd(shell_kind):
    if shell_kind == "legacy":
        tool = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = tool.resolve_permission(BashArgs(command="cd * && git status"))
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
        )
        result = tool.resolve_permission(
            ExperimentalBashArgs(command="cd * && git status")
        )

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
def test_git_reader_inspects_bare_repository_config(shell_kind, tmp_path, monkeypatch):
    (tmp_path / "objects").mkdir()
    (tmp_path / "refs").mkdir()
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "config").write_text("[core]\n\tfsmonitor = ./monitor\n")
    monkeypatch.chdir(tmp_path)
    if shell_kind == "legacy":
        tool = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = tool.resolve_permission(BashArgs(command="git status"))
    else:
        tool = ExperimentalBash(
            config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
        )
        result = tool.resolve_permission(ExperimentalBashArgs(command="git status"))

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    ("command", "expected_reason"),
    [
        ("cat < input.txt", "redirection"),
        ("(PATH=/tmp; git status)", "environment assignments, subshell"),
        ("&&", "a syntax error"),
    ],
    ids=["redirection", "multiple", "parse-error"],
)
def test_shell_approval_prompt_names_the_offending_syntax(
    shell_kind, command, expected_reason
):
    """The prompt must say which construct blocked auto-approval, not just that one did.

    Only reachable where no command pattern survived; where one does it is named
    instead, because it is what the answer grants.
    """
    result = _resolve_default_shell_permission(shell_kind, command)

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    assert any(
        required.label == f"shell syntax requiring approval: {expected_reason}"
        for required in result.required_permissions
    )


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    ("command", "expected_pattern"),
    [
        # Allowlisted, so the pattern only exists to carry the answer.
        ("cat -n $FILE", "cat *"),
        ("echo $HOME", "echo *"),
        ("echo ===", "echo *"),
        # Not allowlisted, so the pattern is the grant.
        ("echo $(npm test)", "npm test *"),
        ("uv run pytest $FILE", "uv run pytest *"),
    ],
    ids=[
        "trailing-expansion",
        "allowlisted-expansion",
        "zsh-word",
        "substituted-command",
        "arity-prefix",
    ],
)
def test_a_readable_command_is_scoped_by_its_pattern_not_the_whole_string(
    shell_kind, command, expected_pattern
):
    """Syntax the analysis reads around varies arguments, and arguments wildcard away.

    Scoping these to the literal string made every session grant a single-use
    allow: the next call differed by a character and asked again.
    """
    result = _resolve_default_shell_permission(shell_kind, command)

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    patterns = {rp.session_pattern for rp in result.required_permissions}
    assert expected_pattern in patterns
    assert command not in patterns


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "git $SUB",
        "uv run $CMD",
        "$RUNNER pytest",
        "PATH=/tmp pytest tests",
        "LD_PRELOAD=/tmp/x.so ls",
        "npm test > $LOGFILE",
        # The argument is the program, so the boundary the arity table reports
        # for the wrapper is not where this command's identity stops.
        "xargs $CMD",
        "timeout 5 $CMD",
        "env $CMD",
        "source $SCRIPT",
        # An environment is not an argument, so no pattern can express it and
        # ``git status *`` would cover every one -- including a HOME whose
        # gitconfig names a pager to run.
        "HOME=/tmp/evil git status",
        "GIT_CONFIG_GLOBAL=/tmp/e git status",
        # The extractor drops the quotes, so the token the pattern is cut from
        # is not the token the shell will run.
        '"git" $SUB',
        'git "status" $X',
        # An -exec payload the extractor cannot read is erased from the very
        # command text the guardrail inspects.
        "find . -exec $CMD {} ;",
    ],
    ids=[
        "subcommand",
        "inside-arity-prefix",
        "program-name",
        "search-path",
        "loader-preload",
        "redirect-target",
        "xargs-wrapper",
        "timeout-wrapper",
        "env-wrapper",
        "sourced-script",
        "home-override",
        "git-config-override",
        "quoted-program",
        "quoted-subcommand",
        "guardrail-exec-payload",
    ],
)
def test_syntax_reaching_the_commands_identity_is_scoped_to_the_exact_string(
    shell_kind, command
):
    """A pattern is only honest while the extract still says what will run.

    ``git $SUB`` extracts to ``git``, whose pattern ``git *`` is every subcommand
    from a call to one of them. Offering it beside the exact string would hand it
    over anyway, so it must not be offered at all.
    """
    result = _resolve_default_shell_permission(shell_kind, command)

    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    patterns = [rp.session_pattern for rp in result.required_permissions]
    assert command in patterns
    # Guardrails add exact-text entries of their own, which release only the call
    # they name; a trailing ``*`` is the widening being avoided.
    assert not [pattern for pattern in patterns if pattern.endswith(" *")]


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
def test_an_unreadable_token_is_recorded_even_when_the_call_needs_other_scopes(
    shell_kind,
):
    """The command's own pattern is never left to ride on an unrelated permission.

    ``grep`` is allowlisted, so the outside-workdir glob is all the call would
    otherwise ask for, and granting "read /etc" would release every future
    ``grep $ANYTHING /etc/hosts``.
    """
    result = _resolve_default_shell_permission(shell_kind, "grep $PATTERN /etc/hosts")

    assert isinstance(result, PermissionContext)
    scopes = {rp.scope for rp in result.required_permissions}
    assert scopes == {
        PermissionScope.COMMAND_PATTERN,
        PermissionScope.OUTSIDE_DIRECTORY,
    }
    assert "grep *" in {rp.session_pattern for rp in result.required_permissions}


def _store_after_granting(shell_kind: str, command: str) -> PermissionStore:
    store = PermissionStore()
    result = _resolve_default_shell_permission(shell_kind, command)
    assert isinstance(result, PermissionContext)
    for rp in result.required_permissions:
        store.add_rule(
            ApprovedRule(
                tool_name="bash", scope=rp.scope, session_pattern=rp.session_pattern
            )
        )
    return store


def _uncovered(
    store: PermissionStore, shell_kind: str, command: str
) -> list[RequiredPermission]:
    result = _resolve_default_shell_permission(shell_kind, command)
    assert isinstance(result, PermissionContext)
    return [rp for rp in result.required_permissions if not store.covers("bash", rp)]


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    ("granted", "guardrailed"),
    [
        ("git log $REF", "git log --ext-diff"),
        ("git log $REF", "git log --output=/tmp/stolen"),
        ("git diff $REF", "git diff --textconv"),
        ("git log $REF *", "git log --ext-diff"),
        ("git diff $REF *", "git diff --textconv"),
    ],
    ids=["ext-diff", "output", "textconv", "glob-ext-diff", "glob-textconv"],
)
def test_a_grant_on_a_guardrailed_command_does_not_cover_its_guarded_options(
    shell_kind, granted, guardrailed
):
    """``git log *`` would cover the very options the guardrail exists to catch.

    ``--ext-diff`` and ``--textconv`` run a driver named in the repository's own
    config and ``--output`` writes wherever it is pointed, so a grant earned by
    an innocuous expansion has to stop short of them. The ``*`` rows are the same
    grant written with a glob of its own, which must stay an argument.
    """
    store = _store_after_granting(shell_kind, granted)

    assert not _uncovered(store, shell_kind, granted)
    assert _uncovered(store, shell_kind, guardrailed)


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    ("granted", "next_call"),
    [
        ("git log --output $FILE", "git log --output $OTHER"),
        ("git log $REF", "git log $OTHER"),
        ("git submodule foreach $CMD", "git submodule foreach $OTHER"),
    ],
    ids=["guarded-option-value", "positional", "foreach-payload"],
)
def test_an_unreadable_token_keeps_a_guardrailed_command_scoped_to_its_own_text(
    shell_kind, granted, next_call
):
    """The extract drops the token, and a literal grant has no ``*`` to lose it in.

    Both calls extract to ``git log --output``, which says nothing about where
    either one writes. A positional is no safer -- ``$REF`` is ``--ext-diff`` if
    the variable says so -- so for a command whose options are guarded, an
    unreadable token anywhere leaves the command as written the only honest scope.
    """
    store = _store_after_granting(shell_kind, granted)

    assert not _uncovered(store, shell_kind, granted)
    assert _uncovered(store, shell_kind, next_call)


def test_a_shell_override_is_not_evidence_that_the_command_itself_got_scoped():
    """A context permission is COMMAND_PATTERN-scoped and says nothing about the command.

    ``[[ ... ]]`` extracts no command at all, so the override was the only thing
    left in the list and the call recorded nothing about what it would test.
    """
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )
    store = PermissionStore()

    granted = tool.resolve_permission(
        ExperimentalBashArgs(command="[[ -n $FOO ]]", shell="/bin/zsh")
    )
    assert isinstance(granted, PermissionContext)
    assert "[[ -n $FOO ]]" in {
        rp.session_pattern for rp in granted.required_permissions
    }
    for rp in granted.required_permissions:
        store.add_rule(
            ApprovedRule(
                tool_name="bash", scope=rp.scope, session_pattern=rp.session_pattern
            )
        )

    other = tool.resolve_permission(
        ExperimentalBashArgs(command="[[ -f $HOME/.ssh/id_rsa ]]", shell="/bin/zsh")
    )
    assert isinstance(other, PermissionContext)
    assert [rp for rp in other.required_permissions if not store.covers("bash", rp)]


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize(
    ("granted", "escalated"),
    [
        ("cat $FILE", "cat notes.txt > /etc/cron.d/pwn"),
        ("echo $MSG", "echo key >> ~/.ssh/authorized_keys"),
        ("ls $DIR", "ls > /tmp/listing"),
    ],
    ids=["cat-redirect", "echo-append", "ls-redirect"],
)
def test_a_pattern_grant_does_not_cover_what_only_the_command_text_can_scope(
    shell_kind, granted, escalated
):
    """``cat *`` is a fair grant, and ``fnmatch`` would let it swallow a redirect.

    A redirect target never reaches ``command_parts``, so it earns no
    outside-workdir scope of its own and the exact command is the only permission
    between an innocuous grant and an arbitrary write.
    """
    store = _store_after_granting(shell_kind, granted)

    assert _uncovered(store, shell_kind, escalated)


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
@pytest.mark.parametrize("command", ["python", "cat"])
def test_a_heredoc_grant_covers_only_the_body_it_was_shown(shell_kind, command):
    """The body is what the command runs, and none of it reaches the extract."""
    store = _store_after_granting(shell_kind, f"{command} <<EOF\nfirst\nEOF")

    assert _uncovered(store, shell_kind, f"{command} <<EOF\nsecond\nEOF")


_WITHHOLDS_APPROVAL = {
    "date": ["date", "--set", "2020-01-01"],
    "du": ["du", "--files0-from=/tmp/x"],
    "file": ["file", "--files-from=/tmp/x"],
    "find": ["find", ".", "-delete"],
    "git": ["git", "log", "--ext-diff"],
    "less": ["less", "--log-file=/tmp/x", "f"],
    "md5sum": ["md5sum", "--check", "/tmp/x"],
    "more": ["more", "--log-file=/tmp/x", "f"],
    "sha1sum": ["sha1sum", "--check", "/tmp/x"],
    "sha256sum": ["sha256sum", "--check", "/tmp/x"],
    "shasum": ["shasum", "--check", "/tmp/x"],
    "sort": ["sort", "--output=/tmp/x", "f"],
    "tree": ["tree", "--output=/tmp/x"],
    "uniq": ["uniq", "input", "/tmp/x"],
    "wc": ["wc", "--files0-from=/tmp/x"],
}
_ONLY_NAMES_PATHS = {
    "diff": ["diff", "--from-file=/etc/hosts", "b"],
    "grep": ["grep", "--file=/etc/hosts", "x"],
}


def test_option_guardrails_track_the_policies_that_can_withhold_approval():
    """``has_option_guardrails`` is what stops a command's grant generalising.

    A policy that only nominates paths has nothing to protect there: those paths
    become OUTSIDE_DIRECTORY permissions, a scope no command pattern is matched
    against, so keeping such a command exact costs prompts and buys nothing.
    """
    assert set(_WITHHOLDS_APPROVAL) | set(_ONLY_NAMES_PATHS) == set(_COMMAND_POLICIES)

    for tokens in _WITHHOLDS_APPROVAL.values():
        assert analyze_shell_command_policy(tokens).requires_approval
        assert has_option_guardrails(tokens)

    for tokens in _ONLY_NAMES_PATHS.values():
        policy = analyze_shell_command_policy(tokens)
        assert not policy.requires_approval
        assert policy.option_path_values
        assert not has_option_guardrails(tokens)


@pytest.mark.parametrize("shell_kind", ["legacy", "managed"])
def test_a_policy_that_only_names_paths_leaves_the_command_generalising(shell_kind):
    """``grep``'s guarded options resolve to paths, not to a withheld approval.

    Those land in OUTSIDE_DIRECTORY, a scope no command pattern is matched
    against, so keeping ``grep`` exact would cost prompts and buy nothing.
    """
    store = _store_after_granting(shell_kind, "grep -rn $PATTERN src")

    assert not _uncovered(store, shell_kind, "grep -rn $OTHER src")
    assert _uncovered(store, shell_kind, "grep --file=/etc/shadow src")


@pytest.mark.parametrize(
    "command",
    [
        "git $SUB",
        "$RUNNER pytest",
        "PATH=/tmp pytest",
        "FOO=bar ls",
        "xargs $CMD",
        '"git" $SUB',
        "echo hi > out.txt",
        "echo hi > $OUT",
        "f() { git $X; }",
        "foo \\\nbar",
        "&&",
    ],
)
def test_an_invalidated_scope_always_carries_a_reason(command):
    """The shells label the exact-string grant with the analysis reason, so every
    route to ``invalidates_scope`` has to leave one behind or the prompt names
    nothing as the syntax that blocked auto-approval.
    """
    analysis = analyze_shell_command(command)

    assert analysis.invalidates_scope
    assert analysis.approval_reasons


def _force_windows_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "vibe.utils.platform.shutil.which",
        lambda name, path=None: (
            "C:\\Program Files\\Git\\bin\\bash.exe" if name == "bash" else None
        ),
    )


def _force_windows_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    monkeypatch.setattr(
        "vibe.utils.platform.shutil.which", lambda name, path=None: None
    )


class TestShellAwareDefaultLists:
    def test_windows_bash_selects_posix_lists(self, monkeypatch):
        _force_windows_bash(monkeypatch)
        assert "ls" in default_read_only_commands()
        assert "dir" not in default_read_only_commands()
        assert "vim" in _get_default_denylist()
        assert "bash" in _get_default_denylist_standalone()

    def test_windows_cmd_selects_windows_lists(self, monkeypatch):
        _force_windows_cmd(monkeypatch)
        assert "dir" in default_read_only_commands()
        assert "ls" not in default_read_only_commands()
        assert "cmd /k" in _get_default_denylist()
        assert "notepad" in _get_default_denylist_standalone()


class TestResolvePermissionShellAware:
    """Permission analysis must run on Windows, gated by the resolved shell."""

    def _make_default_bash(self) -> Bash:
        config = BashToolConfig()
        return Bash(config_getter=lambda: config, state=BaseToolState())

    def test_windows_bash_allowlists_unix_read_only(self, monkeypatch):
        _force_windows_bash(monkeypatch)
        bash_tool = self._make_default_bash()
        result = bash_tool.resolve_permission(BashArgs(command="ls -la"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_windows_bash_denies_interactive_editor(self, monkeypatch):
        _force_windows_bash(monkeypatch)
        bash_tool = self._make_default_bash()
        result = bash_tool.resolve_permission(BashArgs(command="vim notes.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_windows_bash_asks_for_unknown_command(self, monkeypatch):
        _force_windows_bash(monkeypatch)
        bash_tool = self._make_default_bash()
        result = bash_tool.resolve_permission(BashArgs(command="frobnicate --now"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK

    def test_windows_cmd_defers_analysis_for_read_only(self, monkeypatch):
        _force_windows_cmd(monkeypatch)
        bash_tool = self._make_default_bash()
        # cmd.exe is not parsed with the bash grammar; defer to config (ASK).
        assert bash_tool.resolve_permission(BashArgs(command="dir /s /b")) is None

    def test_windows_cmd_defers_analysis_for_dangerous(self, monkeypatch):
        _force_windows_cmd(monkeypatch)
        bash_tool = self._make_default_bash()
        # Not NEVER-blocked via a bash parse; the human is asked instead.
        assert bash_tool.resolve_permission(BashArgs(command="cmd /k whoami")) is None


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.parametrize(
    "command",
    [
        "python3 << 'EOF'\nprint(42)\nEOF",
        "python3 - << 'EOF'\nprint(42)\nEOF",
        "python3 <<'PYEOF'\nimport sys\nprint('hello')\nPYEOF",
        "python3 < input.txt",
        "cd . && python3 <<'EOF'\nprint(42)\nEOF",
        "cd . && python3 - <<'EOF'\nprint(42)\nEOF",
        "false || python3 - <<'EOF'\nprint(42)\nEOF",
        "printf ignored | python3 - <<'EOF'\nprint(42)\nEOF",
        "cd . && printf ignored | python3 - <<'EOF'\nprint(42)\nEOF",
    ],
)
def test_experimental_bash_standalone_denylisted_with_redirect_not_denied(command):
    bash_tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )
    result = bash_tool.resolve_permission(ExperimentalBashArgs(command=command))
    assert isinstance(result, PermissionContext)
    assert result.permission is not ToolPermission.NEVER, (
        f"Command with redirect should not be denied: {command!r}"
    )


@pytest.mark.parametrize("operator", ["&&", "||", "|"])
def test_redirect_on_later_command_does_not_allow_bare_python(bash, operator):
    result = bash.resolve_permission(
        BashArgs(command=f"python3 {operator} cat <<'EOF'\nhello\nEOF")
    )
    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.NEVER


_AGENT = "/Users/me/Library/LaunchAgents/x.plist"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (f"cat tools/plist.tmpl <workspace>{_AGENT}", ((_AGENT, _AGENT),)),
        (
            'echo x > "/Users/me/my notes.txt"',
            (('"/Users/me/my notes.txt"', "/Users/me/my notes.txt"),),
        ),
        ("echo x > '/Users/me/$x'", (("'/Users/me/$x'", "/Users/me/$x"),)),
        ("make >/dev/null 2> /tmp/err.log", (("/tmp/err.log", "/tmp/err.log"),)),
        ("echo x >&/var/log/both.log", (("/var/log/both.log", "/var/log/both.log"),)),
        ("cat <<EOF > /etc/motd\nhi\nEOF", (("/etc/motd", "/etc/motd"),)),
        (
            "echo $(echo x > /a/inner) > /a/outer",
            (("/a/inner", "/a/inner"), ("/a/outer", "/a/outer")),
        ),
        ("echo x > /a/out; echo y >> /a/out", (("/a/out", "/a/out"),)),
        # Only the word after the operator is opened; the rest are arguments.
        ("echo x > /tmp/a /etc/passwd", (("/tmp/a", "/tmp/a"),)),
        # From a root, but only the shell can read the spelling.
        ("echo x > /tmp/$NAME.log", (("/tmp/$NAME.log", None),)),
        ("echo x > /var/*.log", (("/var/*.log", None),)),
        ('echo x > /tmp/"a b"', (('/tmp/"a b"', None),)),
        ("echo x > $'/Users/me/x'", (("$'/Users/me/x'", None),)),
        ("echo x > $'\\x2fetc/x'", (("$'\\x2fetc/x'", None),)),
        ("echo x > \\/etc/x", (("\\/etc/x", None),)),
        # Neither from a root nor relative: only the shell knows where it lands.
        # zsh expands an unquoted ``=cmd`` to the command's path.
        ("echo x > =git", (("=git", None),)),
        ("echo x > >(tee /etc/passwd)", ((">(tee /etc/passwd)", None),)),
        ("echo x > *.log", (("*.log", None),)),
        # Relative, ``~`` or expansion-led: moved by what the command sets.
        ("make > build.log 2>&1", ()),
        ("echo x >> ~/.zshrc", ()),
        ("echo x > $OUT/y", ()),
        ('echo x > "$HOME/y"', ()),
        ("echo x > out_$(date).log", ()),
        ("echo x > '=git'", ()),
        # A POSIX shell reads a drive spelling as a relative path.
        ("echo x > C:/Users/me/x.ps1", ()),
        # Reads, streams and descriptors open no file to keep.
        ("cat < /etc/passwd", ()),
        ("echo x >&2; echo y >&-", ()),
        ("echo x > /dev/stderr 2>/dev/null", ()),
    ],
)
def test_redirect_writes_place_only_plainly_spelled_targets_from_a_root(
    command, expected
):
    """A target from a root is placed where the shell can only read it one way.
    Relative, ``~`` and expansion-led targets, reads, streams and descriptor
    operations are not listed; every other target is listed unplaced.
    """
    writes = redirect_writes(command)

    assert tuple((write.spelling, write.path) for write in writes) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # Bash joins the lines and opens ``/Users/me/.ssh/authorized_keys``.
        (
            "echo x > /Users/me/.ssh/authori\\\nzed_keys",
            (("/Users/me/.ssh/authori", None),),
        ),
        # Bash opens ``/work/space/../../etc/x``.
        ("echo x > /work/space/\\\n../../etc/x", (("/work/space/", None),)),
        # Relative too: the parse says nothing certain about any target.
        ("make \\\n  > build.log", (("build.log", None),)),
        ("exec 3<> /etc/hosts", (("/etc/hosts", None),)),
        # A ``$(...)`` inside double quotes is code, continued lines and all.
        (
            'echo "$(echo x > /etc/y \\\n z)" > /tmp/q',
            (("/etc/y", None), ("/tmp/q", None)),
        ),
    ],
)
def test_no_write_is_placed_in_a_command_not_read_as_the_shell_reads_it(
    command, expected
):
    """After a line continuation in code or a syntax error, the grammar's words
    are not the shell's, so every write the command makes is listed unplaced.
    """
    writes = redirect_writes(command)

    assert tuple((write.spelling, write.path) for write in writes) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cat > Dockerfile <<'EOF'\nRUN a \\\n  b\nEOF", ()),
        ("cat > /tmp/x <<EOF\nline \\\nmore\nEOF", (("/tmp/x", "/tmp/x"),)),
        ("echo 'a\\\nb' > /tmp/q", (("/tmp/q", "/tmp/q"),)),
        ("pytest \\\n  -x 2>&1 | tail", ()),
        ("# note \\\nmake > /tmp/out.log", (("/tmp/out.log", "/tmp/out.log"),)),
        ('echo "a\\\nb" > /tmp/x', (("/tmp/x", "/tmp/x"),)),
    ],
)
def test_a_continued_line_in_data_or_without_a_write_places_as_usual(command, expected):
    """A backslash-newline in a heredoc body, quoted text or a comment joins no
    words of code, and a command that writes no file has nothing to place.
    """
    writes = redirect_writes(command)

    assert tuple((write.spelling, write.path) for write in writes) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("echo x > C:/Users/me/x.ps1", (("C:/Users/me/x.ps1", "C:/Users/me/x.ps1"),)),
        (
            "echo x > 'C:\\Users\\me\\x.ps1'",
            (("'C:\\Users\\me\\x.ps1'", "C:\\Users\\me\\x.ps1"),),
        ),
        ("echo x > /c/Users/me/x.ps1", (("/c/Users/me/x.ps1", "/c/Users/me/x.ps1"),)),
        # Unquoted, Git Bash reads the backslashes as escapes: ``C:Usersmex.ps1``.
        ("echo x > C:\\Users\\me\\x.ps1", (("C:\\Users\\me\\x.ps1", None),)),
    ],
)
def test_git_bash_also_writes_from_drive_roots(command, expected):
    """Git Bash opens a drive spelling from that drive's root."""
    writes = redirect_writes(command, drive_paths=True)

    assert tuple((write.spelling, write.path) for write in writes) == expected
