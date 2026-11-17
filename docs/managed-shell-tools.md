# Managed shell tools

The built-in shell surface is controlled by the `managed_shell_tools_enabled` config
field and the `vibe_cli_managed_shell_tools` GrowthBook experiment. The default variant
keeps the legacy one-shot `bash` tool, including its existing Windows behavior.
The managed variant exposes OS-native shell tools:
POSIX systems, including WSL where Vibe runs as Linux, get managed `bash`,
`bash_output`, `bash_stdin`, `bash_sessions`, and `bash_log_file`; native Windows
gets `git_bash`, `git_bash_output`, `git_bash_stdin`, `git_bash_sessions`, and
`git_bash_log_file` when Git Bash is available. If Git Bash is unavailable,
native Windows falls back to `powershell`, `powershell_output`,
`powershell_stdin`, `powershell_sessions`, and `powershell_log_file`.

Managed shell sessions return a `session_id`, inline output, a cursor for polling
more output, and a log path under `~/.vibe/shell-tool/sessions/`. Long-running
commands can be left alive with `background = true`, and interactive commands can
be driven with the matching stdin tool.

POSIX `bash` reads permissions, allowlists, and denylists from `[tools.bash]`.
Supported POSIX and PowerShell readers also apply `sensitive_file_patterns`
(the `.env` family by default) and require file-scoped approval before reading
a match, including allowlisted commands. Wrappers such as `env`, `nice`,
`nohup`, `stdbuf`, `time` and `timeout` are read through to the command they
run. The exact command must be approved instead when a read cannot be bound to
known files: dynamic, brace-expanded or unsupported-glob operands, opaque
interpreter invocations (`sh -c`, `xargs`), recursive readers, sensitive reads
after a literal `cd`, reads after an opaque directory change, and PowerShell
readers fed paths through a pipe or given ambiguous options. PowerShell `-Path`
expands wildcards; `-LiteralPath` reads the literal path. Setting
`sensitive_file_patterns = []` disables all of these checks.
Native Windows `git_bash` reads them from `[tools.git_bash]`; native Windows
`powershell` reads them from `[tools.powershell]`. Neither Windows tool reads
`[tools.bash]`. Git Bash is preferred when Vibe can resolve a usable `bash.exe`
from PATH, Git for Windows, or standard Git install locations. If Git Bash is
unavailable, the PowerShell resolution order is `pwsh.exe`, then
`powershell.exe`. `cmd.exe` is not used by the managed Windows shell tools.

```toml
[tools.git_bash]
permission = "ask"
shell = "C:\\Program Files\\Git\\bin\\bash.exe"

[tools.powershell]
permission = "ask"
shell = "powershell.exe"
```

The rollout assignment is server-managed and is not a `config.toml` option.
