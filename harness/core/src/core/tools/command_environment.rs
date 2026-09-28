use serde::{Deserialize, Serialize};

const UNIX_DESCRIPTION: &str =
    "Run a shell command and capture stdout, stderr, and its return code.";

const GIT_BASH_DESCRIPTION: &str = r#"Use `bash` to run a one-off Git Bash command on native Windows and capture its output.

- Each command runs independently in a fresh, stateless Git Bash process.
- Use POSIX shell syntax and Unix-style command chaining, redirects, variables, and quoting, not PowerShell or cmd.exe syntax.
- Standard output and standard error are captured separately. Read `returncode` to determine whether the command succeeded.
- Use `timeout_seconds` to control how long the command may run.
- Prefer `read_file`, `write_file`, and `edit` over shell equivalents when they can perform the operation directly."#;

const POWERSHELL_DESCRIPTION: &str = r#"Use `bash` to run a one-off PowerShell command on native Windows and capture its output.

- Each command runs independently in a fresh, stateless PowerShell process.
- Use PowerShell syntax, cmdlets, pipelines, variables, quoting, and path conventions, not Bash or cmd.exe syntax.
- Standard output and standard error are captured separately. Read `returncode` to determine whether the command succeeded.
- Use `timeout_seconds` to control how long the command may run.
- Prefer `read_file`, `write_file`, and `edit` over shell equivalents when they can perform the operation directly."#;

const IN_MEMORY_BASH_DESCRIPTION: &str = r#"Execute a Bash command in a lightweight virtual filesystem environment and return its output.

## Supported Commands

File Operations
cat, cp, file, ln, ls, mkdir, mv, readlink, rm, rmdir, split, stat, touch, tree

Text Processing
awk, base64, column, comm, cut, diff, expand, fold, grep, head, join, md5sum, nl, od, paste, printf, rev, rg, sed, sort, strings, tac, tail, tr, unexpand, uniq, wc, xargs

Data Processing
jq (JSON)

Navigation & Environment
basename, cd, dirname, du, echo, env, export, find, hostname, printenv, pwd, tee

Shell Utilities
alias, bash, chmod, clear, date, expr, false, help, history, seq, sh, true, unalias, which, whoami

Notes
- Node.js and Python interpreters are not available in this environment. Do not use `node`, `python`, or `python3`.
- Each command runs independently. Files persist between calls, but the working directory, environment variables, aliases, and functions do not.
- `egrep` and `fgrep` are available as `grep -E` and `grep -F` respectively.
- Avoid jq string interpolation that embeds literal double quotes inside a shell-quoted filter because that quoting pattern is not reliably supported. Prefer selecting fields separately or use an encoding such as `@tsv` when combining text output.
- When using jq and xargs together, use `xargs -I {}` for reliable argument replacement.
- All listed commands support `--help` for usage information.

## Shell Features

Pipes: cmd1 | cmd2
Redirections: >, >>, 2>, 2>&1, <
Command chaining: &&, ||, ;
Variables: $VAR, ${VAR}, ${VAR:-default}
Positional parameters: $1, $2, $@, $#
Glob patterns: *, ?, [...]
If statements: if COND; then CMD; elif COND; then CMD; else CMD; fi
Functions: function name { ... } or name() { ... }
Local variables: local VAR=value
Loops: for, while, until
Symbolic links: ln -s target link
Hard links: ln target link"#;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum CommandEnvironment {
    Disabled,
    Unix,
    GitBash,
    #[serde(rename = "powershell")]
    PowerShell,
    InMemoryBash,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct CommandEnvironmentProfile {
    pub(crate) tool_name: &'static str,
    pub(crate) tool_description: &'static str,
}

impl CommandEnvironment {
    pub(crate) fn profile(self) -> Option<CommandEnvironmentProfile> {
        match self {
            Self::Disabled => None,
            Self::Unix => Some(CommandEnvironmentProfile {
                tool_name: "bash",
                tool_description: UNIX_DESCRIPTION,
            }),
            Self::GitBash => Some(CommandEnvironmentProfile {
                tool_name: "bash",
                tool_description: GIT_BASH_DESCRIPTION,
            }),
            Self::PowerShell => Some(CommandEnvironmentProfile {
                tool_name: "bash",
                tool_description: POWERSHELL_DESCRIPTION,
            }),
            Self::InMemoryBash => Some(CommandEnvironmentProfile {
                tool_name: "bash",
                tool_description: IN_MEMORY_BASH_DESCRIPTION,
            }),
        }
    }

    pub(crate) fn is_enabled(self) -> bool {
        self != Self::Disabled
    }

    pub(crate) fn changes_system_prompt(self, next: Self) -> bool {
        self != next
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn command_environment_uses_tagged_snake_case_modes() {
        // Prepare
        let environments = [
            (CommandEnvironment::Disabled, "disabled"),
            (CommandEnvironment::Unix, "unix"),
            (CommandEnvironment::GitBash, "git_bash"),
            (CommandEnvironment::PowerShell, "powershell"),
            (CommandEnvironment::InMemoryBash, "in_memory_bash"),
        ];

        // Do
        let encoded =
            environments.map(|(environment, _)| serde_json::to_value(environment).unwrap());

        // Assert
        assert_eq!(encoded, environments.map(|(_, mode)| json!({"mode": mode})));
    }
}
