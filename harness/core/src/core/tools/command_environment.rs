use serde::{Deserialize, Serialize};

// What the model is told about long-running work, only where the process tools
// (`tools.process.*`) are available: background processes are enabled and the
// shell can run them (see `CommandEnvironment::background_advice`), that is
// Unix, Git Bash and PowerShell. This is the one copy: the bash tool here uses
// the description and the timeout hint, and the Hosts read the timeout advice
// and the shell guidance through the Python binding.

/// How long a bash call may run: the plain line, or, with background processes,
/// where longer work goes instead.
macro_rules! timeout_text {
    (foreground) => {
        "Use `timeout_seconds` to control how long the command may run."
    };
    (background) => {
        "The call blocks until the command exits, and a command still running after `timeout_seconds` is stopped. Start long-running commands, such as servers and long builds, with `tools.process.start` and read their output with `tools.process.output`."
    };
}

macro_rules! unix_description {
    (foreground) => {
        "Run a shell command and capture stdout, stderr, and its return code."
    };
    (background) => {
        concat!(
            unix_description!(foreground),
            "\n\n",
            timeout_text!(background)
        )
    };
}

macro_rules! git_bash_description {
    ($mode:ident) => {
        concat!(
            r#"Use `bash` to run a one-off Git Bash command on native Windows and capture its output.

- Each command runs independently in a fresh, stateless Git Bash process.
- Use POSIX shell syntax and Unix-style command chaining, redirects, variables, and quoting, not PowerShell or cmd.exe syntax.
- Write Windows drives as `/c/...` with forward slashes, never `C:\...`.
- Standard output and standard error are captured separately. Read `returncode` to determine whether the command succeeded.
- Do not discard stderr (`2>/dev/null`) on exploratory commands like `find` or `ls`: an empty result would then hide a wrong path.
- "#,
            timeout_text!($mode),
            r#"
- Prefer `read_file`, `write_file`, and `edit` over shell equivalents when they can perform the operation directly."#
        )
    };
}

macro_rules! powershell_description {
    ($mode:ident) => {
        concat!(
            r#"Use `bash` to run a one-off PowerShell command on native Windows and capture its output.

- Each command runs independently in a fresh, stateless PowerShell process.
- Use PowerShell syntax, cmdlets, pipelines, variables, quoting, and path conventions, not Bash or cmd.exe syntax.
- Standard output and standard error are captured separately. Read `returncode` to determine whether the command succeeded.
- "#,
            timeout_text!($mode),
            r#"
- Prefer `read_file`, `write_file`, and `edit` over shell equivalents when they can perform the operation directly."#
        )
    };
}

/// Ends the `timeout_seconds` description, after the default and maximum, when
/// background processes are enabled.
const TIMEOUT_HINT: &str = "For longer work, use `tools.process.start` instead.";

/// Ends the note on a bash command stopped at its timeout.
const TIMEOUT_NOTE_ADVICE: &str = "To give it more time, start it with `tools.process.start` and read its output with `tools.process.output`.";

/// Replaces a Vibe Code prompt's shell guidance, which says not to start
/// long-running processes.
const SHELL_GUIDANCE: &str = "Always add timeouts. Start long-running commands, such as servers and long builds, with `tools.process.start` and read their output with `tools.process.output`.";

const UNIX_DESCRIPTION: &str = unix_description!(foreground);

const GIT_BASH_DESCRIPTION: &str = git_bash_description!(foreground);

const POWERSHELL_DESCRIPTION: &str = powershell_description!(foreground);

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
    /// Advice to start long work in the background, if this shell can.
    ///
    /// Only used when background processes are enabled, so the model is never
    /// told to background work where nothing keeps it running.
    pub(crate) background_advice: Option<BackgroundAdvice>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct BackgroundAdvice {
    /// Replaces `tool_description`.
    pub(crate) tool_description: &'static str,
    /// Ends the bash `timeout_seconds` description.
    pub(crate) timeout_hint: &'static str,
    /// Ends the note on a bash command stopped at its timeout.
    pub(crate) timeout_note_advice: &'static str,
    /// Replaces a Vibe Code prompt's shell guidance.
    pub(crate) shell_guidance: &'static str,
}

impl BackgroundAdvice {
    /// The advice for a shell whose bash description, with background
    /// processes, is `tool_description`; the rest is the same for every shell.
    const fn with_description(tool_description: &'static str) -> Self {
        Self {
            tool_description,
            timeout_hint: TIMEOUT_HINT,
            timeout_note_advice: TIMEOUT_NOTE_ADVICE,
            shell_guidance: SHELL_GUIDANCE,
        }
    }
}

impl CommandEnvironmentProfile {
    /// The bash description, with the background advice when `background` is on.
    pub(crate) fn bash_description(self, background: bool) -> &'static str {
        match self.background_advice {
            Some(advice) if background => advice.tool_description,
            _ => self.tool_description,
        }
    }

    /// The sentence that ends `timeout_seconds`, when `background` is on.
    pub(crate) fn timeout_hint(self, background: bool) -> Option<&'static str> {
        self.background_advice
            .filter(|_| background)
            .map(|advice| advice.timeout_hint)
    }
}

impl CommandEnvironment {
    pub(crate) fn profile(self) -> Option<CommandEnvironmentProfile> {
        match self {
            Self::Disabled => None,
            Self::Unix => Some(CommandEnvironmentProfile {
                tool_name: "bash",
                tool_description: UNIX_DESCRIPTION,
                background_advice: Some(BackgroundAdvice::with_description(unix_description!(
                    background
                ))),
            }),
            Self::GitBash => Some(CommandEnvironmentProfile {
                tool_name: "bash",
                tool_description: GIT_BASH_DESCRIPTION,
                background_advice: Some(BackgroundAdvice::with_description(git_bash_description!(
                    background
                ))),
            }),
            Self::PowerShell => Some(CommandEnvironmentProfile {
                tool_name: "bash",
                tool_description: POWERSHELL_DESCRIPTION,
                background_advice: Some(BackgroundAdvice::with_description(
                    powershell_description!(background),
                )),
            }),
            Self::InMemoryBash => Some(CommandEnvironmentProfile {
                tool_name: "bash",
                tool_description: IN_MEMORY_BASH_DESCRIPTION,
                background_advice: None,
            }),
        }
    }

    /// The advice to start long work with the process tools, where they are
    /// available: `background` processes are enabled and this shell can run them.
    pub(crate) fn background_advice(self, background: bool) -> Option<BackgroundAdvice> {
        self.profile()?.background_advice.filter(|_| background)
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

    #[test]
    fn only_a_shell_that_runs_background_processes_advises_the_process_tools() {
        // Prepare
        let environments = [
            CommandEnvironment::Disabled,
            CommandEnvironment::Unix,
            CommandEnvironment::GitBash,
            CommandEnvironment::PowerShell,
            CommandEnvironment::InMemoryBash,
        ];

        for environment in environments {
            for background in [false, true] {
                // Do
                let advice = environment.background_advice(background);

                // Assert
                let advised = background
                    && matches!(
                        environment,
                        CommandEnvironment::Unix
                            | CommandEnvironment::GitBash
                            | CommandEnvironment::PowerShell
                    );
                assert_eq!(advice.is_some(), advised, "{environment:?} {background}");
                if let Some(advice) = advice {
                    assert!(advice.timeout_note_advice.contains("tools.process.start"));
                    assert!(advice.shell_guidance.contains("tools.process.start"));
                }
            }
        }
    }
}
