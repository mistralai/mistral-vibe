//! Typed CLI arguments.

use std::{
    fmt, io,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result};
use clap::{ArgAction, Parser};

use crate::server::{AgentConfig, WorktreeInput};

const DELETED_CWD_ERROR: &str = "Error: Current working directory no longer exists.\n\n\
The directory you started vibe from has been deleted. Please change to an existing directory \
and try again, or use --workdir to specify a working directory.";

/// Output format for headless mode.
#[derive(Clone, Debug, PartialEq, Eq, Default, clap::ValueEnum)]
#[value(rename_all = "lowercase")]
pub enum OutputFormat {
    #[default]
    Text,
    Json,
    Streaming,
    /// Nothing on stdout or stderr: the final response rides the report (`vibe run-server`).
    #[value(skip)]
    Silent,
}

/// Each option `overrides_with` itself, so a repeated flag keeps its last
/// value instead of failing the parse; the `mcp` subcommands stay strict.
#[derive(Parser, Debug)]
#[command(name = "vibe", bin_name = "vibe", version = env!("CARGO_PKG_VERSION"), disable_version_flag = true, about = "Run the Mistral Vibe interactive CLI", after_help = "Commands:\n  update         Check for a Vibe update now (same as --check-upgrade).")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<CliCommand>,

    /// Show version and exit.
    #[arg(short = 'v', long = "version", action = ArgAction::Version)]
    _version: Option<bool>,

    /// Change to this directory before running.
    #[arg(long = "workdir", env = "VIBE_CWD", overrides_with = "cwd")]
    pub cwd: Option<PathBuf>,

    /// Continue from the most recent saved session.
    #[arg(short = 'c', long = "continue", action = ArgAction::SetTrue, conflicts_with = "resume", overrides_with = "continue_session")]
    pub continue_session: bool,

    /// Resume a session. Without SESSION_ID, shows an interactive picker.
    #[arg(long = "resume", num_args = 0..=1, conflicts_with = "continue_session", overrides_with = "resume")]
    pub resume: Option<Option<String>>,

    /// Setup API key and exit.
    #[arg(long = "setup", action = ArgAction::SetTrue, overrides_with = "setup")]
    pub setup: bool,

    /// Initial prompt for the interactive session, or the prompt in headless mode.
    #[arg(num_args = 0..=1)]
    pub initial_prompt: Option<String>,

    /// Run in headless (programmatic) mode: send prompt, output response, exit.
    #[arg(short = 'p', long = "prompt", num_args = 0..=1, overrides_with = "prompt")]
    pub prompt: Option<Option<String>>,

    /// Output format for headless mode.
    #[arg(
        long = "output",
        value_enum,
        default_value = "text",
        overrides_with = "output"
    )]
    pub output: OutputFormat,

    /// Maximum number of assistant turns (headless mode only).
    #[arg(long = "max-turns", overrides_with = "max_turns")]
    pub max_turns: Option<u32>,

    /// Maximum cost in dollars (headless mode only).
    #[arg(long = "max-price", overrides_with = "max_price")]
    pub max_price: Option<f64>,

    /// Maximum total prompt + completion tokens (headless mode only).
    #[arg(long = "max-tokens", overrides_with = "max_tokens")]
    pub max_tokens: Option<u64>,

    /// Interrupt the run once SECONDS have passed since start, write the
    /// export and exit 3. SIGTERM stops the run the same way.
    #[arg(
        long = "time-limit",
        value_name = "SECONDS",
        overrides_with = "time_limit"
    )]
    pub time_limit: Option<f64>,

    /// Run in programmatic mode with the prompt read from PATH, instead of
    /// -p TEXT or stdin.
    #[arg(
        long = "prompt-file",
        value_name = "PATH",
        overrides_with = "prompt_file"
    )]
    pub prompt_file: Option<PathBuf>,

    /// On every exit, write export.json (Vibe version, effective config,
    /// token usage, cost, stop reason, outcome) and a copy of the session
    /// journal (session/) to DIR.
    #[arg(long = "output-dir", value_name = "DIR", overrides_with = "output_dir")]
    pub output_dir: Option<PathBuf>,

    /// Agent to use (builtin: ask, accept-edits, smart-approve, auto-approve,
    /// or custom from ~/.vibe/agents/NAME.toml). Defaults to the 'default_agent'
    /// config setting.
    #[arg(long = "agent", value_name = "NAME", overrides_with = "agent")]
    pub agent: Option<String>,

    /// Approves all tool calls without prompting for the selected agent.
    #[arg(long = "auto-approve", visible_alias = "yolo", action = ArgAction::SetTrue, overrides_with = "auto_approve")]
    pub auto_approve: bool,

    /// Classify each tool call and auto-run the safe ones, prompting only for
    /// risky ones.
    #[arg(long = "smart-approve", action = ArgAction::SetTrue, overrides_with = "smart_approve")]
    pub smart_approve: bool,

    /// Enable specific tools (headless: disables all others). Repeat the flag.
    #[arg(long = "enabled-tools")]
    pub enabled_tools: Vec<String>,

    /// Disable specific tools after --enabled-tools filtering. Repeat the flag.
    #[arg(long = "disabled-tools")]
    pub disabled_tools: Vec<String>,

    /// Trust the working directory for this invocation only.
    #[arg(long = "trust", action = ArgAction::SetTrue, overrides_with = "trust")]
    pub trust: bool,

    /// Run inside a git worktree under $VIBE_HOME/worktrees. With NAME,
    /// create (or reuse) a worktree and branch named NAME. Without NAME,
    /// create a new one named after the prompt (or a random slug). Combines
    /// with `-c`/`--resume`: the worktree is prepared first and
    /// the resume is then scoped to the worktree directory.
    #[arg(long = "worktree", num_args = 0..=1, overrides_with = "worktree")]
    pub worktree: Option<Option<String>>,

    /// Additional working directory for file access. Repeat the flag.
    #[arg(long = "add-dir")]
    pub add_dir: Vec<PathBuf>,

    /// Feature flag for teleport (hidden).
    #[arg(long = "teleport", action = ArgAction::SetTrue, hide = true, conflicts_with = "prompt", overrides_with = "teleport")]
    pub teleport: bool,

    /// Run the session's tools through the agent server on this Unix socket (experimental, hidden).
    #[arg(long = "agent-socket", value_name = "PATH", hide = true)]
    pub agent_socket: Option<PathBuf>,

    /// Check for a Vibe update now, prompt to install it, and exit.
    #[arg(long = "check-upgrade", action = ArgAction::SetTrue, overrides_with = "check_upgrade")]
    pub check_upgrade: bool,
}

/// A leading bare `update` is the `--check-upgrade`
/// command. Only a first-position exact match rewrites — `-- update` and
/// `--prompt update` stay untouched. The vector includes the program name at
/// index 0, matching the argv `Cli::parse_from` expects.
pub fn rewrite_update_argv(args: Vec<std::ffi::OsString>) -> Vec<std::ffi::OsString> {
    let mut args = args;
    if args
        .get(1)
        .is_some_and(|first| first.as_os_str() == std::ffi::OsStr::new("update"))
    {
        args[1] = std::ffi::OsString::from("--check-upgrade");
    }
    args
}

#[derive(Debug, clap::Subcommand)]
pub enum CliCommand {
    /// Manage MCP server configuration without starting a session.
    Mcp {
        #[command(subcommand)]
        command: crate::mcp_command::McpCommand,
    },
    /// Serve many headless runs over JSON-RPC on stdin and stdout (experimental).
    #[command(hide = true)]
    RunServer {
        /// Refuse a run while this many are live.
        #[arg(long = "max-runs", value_name = "N", value_parser = clap::value_parser!(u64).range(1..))]
        max_runs: Option<u64>,
        /// Runs one app-server process serves at most.
        #[arg(long = "runs-per-app-server", value_name = "K", default_value_t = 16, value_parser = clap::value_parser!(u64).range(1..))]
        runs_per_app_server: u64,
    },
}

#[derive(Debug)]
pub enum WorkingDirectoryError {
    Deleted(io::Error),
    Current(io::Error),
    Override { path: PathBuf, source: io::Error },
}

impl fmt::Display for WorkingDirectoryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Deleted(_) => formatter.write_str(DELETED_CWD_ERROR),
            Self::Current(source) => {
                write!(
                    formatter,
                    "Error: Could not read current working directory: {source}"
                )
            }
            Self::Override { path, source } => {
                write!(
                    formatter,
                    "Error: Could not use --workdir {}: {source}",
                    path.display()
                )
            }
        }
    }
}

impl std::error::Error for WorkingDirectoryError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Deleted(source) | Self::Current(source) => Some(source),
            Self::Override { source, .. } => Some(source),
        }
    }
}

/// Resolve the session cwd and apply an explicit override to the process.
pub fn resolve_working_directory(cwd: Option<PathBuf>) -> Result<PathBuf, WorkingDirectoryError> {
    let Some(path) = cwd else {
        return std::env::current_dir().map_err(|source| {
            if source.kind() == io::ErrorKind::NotFound {
                WorkingDirectoryError::Deleted(source)
            } else {
                WorkingDirectoryError::Current(source)
            }
        });
    };
    let resolved = path
        .canonicalize()
        .map_err(|source| WorkingDirectoryError::Override {
            path: path.clone(),
            source,
        })?;
    std::env::set_current_dir(&resolved)
        .map_err(|source| WorkingDirectoryError::Override { path, source })?;
    Ok(resolved)
}

fn nonempty_env(name: &str) -> Option<std::ffi::OsString> {
    std::env::var_os(name).filter(|value| !value.is_empty())
}

#[cfg(not(windows))]
fn user_home() -> Option<PathBuf> {
    nonempty_env("HOME").map(PathBuf::from).or_else(|| {
        #[allow(deprecated)]
        std::env::home_dir().filter(|path| !path.as_os_str().is_empty())
    })
}

#[cfg(windows)]
fn user_home() -> Option<PathBuf> {
    nonempty_env("USERPROFILE")
        .map(PathBuf::from)
        .or_else(|| {
            let mut drive = nonempty_env("HOMEDRIVE")?;
            drive.push(nonempty_env("HOMEPATH")?);
            Some(PathBuf::from(drive))
        })
        .or_else(|| nonempty_env("HOME").map(PathBuf::from))
}

/// Expand a leading `~` and make the path absolute against the invocation
/// directory, before the `--workdir` chdir. A path that does not exist yet
/// still resolves: the output directory is created on write.
fn resolve_invocation_path(path: &Path, invocation_cwd: &Path) -> PathBuf {
    let expanded =
        resolve_user_path(path, user_home().as_deref()).unwrap_or_else(|_| path.to_path_buf());
    if expanded.is_absolute() {
        expanded
    } else {
        invocation_cwd.join(expanded)
    }
}

/// Expand a leading `~`, rejecting it when no user home can be resolved.
pub fn resolve_user_path(path: &Path, home: Option<&Path>) -> Result<PathBuf> {
    let Ok(suffix) = path.strip_prefix("~") else {
        return Ok(path.to_path_buf());
    };
    let home = home.with_context(|| {
        format!(
            "cannot resolve home directory for --workdir: {}",
            path.display()
        )
    })?;
    Ok(home.join(suffix))
}

/// Convert the workdir for the JSON protocol without lossy path rewriting.
pub fn workdir_to_string(path: &Path) -> Result<String> {
    path.to_str()
        .map(str::to_owned)
        .context("working directory path is not valid UTF-8")
}

/// The startup session intent parsed from the CLI flags: `--continue`,
/// `--resume <id>`, `--resume` (picker), or none.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub enum StartupResume {
    #[default]
    None,
    /// `--resume` with no id opens the saved-session picker.
    Picker,
    /// `--resume <id>` resumes that exact session.
    ById(String),
    /// `--continue` resumes the most recent saved session.
    Continue,
}

impl StartupResume {
    /// The saved session this intent attaches, once `--continue` has been
    /// resolved against the session list. `None` keeps the session
    /// `session/start` opened.
    pub fn target(&self, continue_session_id: Option<String>) -> Option<String> {
        match self {
            Self::None | Self::Picker => None,
            Self::ById(id) => Some(id.clone()),
            Self::Continue => continue_session_id,
        }
    }
}

/// The session a headless run works in: `-c` continues the latest one,
/// `--resume <id>` that exact one.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub enum HeadlessSession {
    #[default]
    New,
    Continue,
    Resume(String),
}

/// Parsed headless-mode options.
#[derive(Clone, Debug)]
pub struct HeadlessOptions {
    pub prompt: String,
    pub session: HeadlessSession,
    pub output: OutputFormat,
    pub max_turns: Option<u32>,
    pub max_price: Option<f64>,
    pub max_tokens: Option<u64>,
    pub time_limit: Option<f64>,
    pub output_dir: Option<PathBuf>,
    pub agent: Option<String>,
    pub auto_approve: bool,
    pub enabled_tools: Vec<String>,
    pub disabled_tools: Vec<String>,
    pub trust: bool,
    pub add_dir: Vec<PathBuf>,
    pub worktree: Option<WorktreeInput>,
    /// `--agent-socket PATH`, handed to the app-server, which connects.
    pub agent_socket: Option<PathBuf>,
}

/// The execution-control config resolved from the CLI flags, sent to the
/// app-server in `agentConfig`. `--auto-approve`/`--yolo` is a session-wide
/// bypass and never swaps the agent.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ExecutionControl {
    /// Resolved agent name; `None` lets the server use `default_agent`.
    pub agent: Option<String>,
    /// Approve every tool call (`--auto-approve` / `--yolo`).
    pub auto_approve: bool,
}

impl Cli {
    /// Resolve the headless paths against the invocation directory, before
    /// `--workdir` changes it: a relative `--output-dir` or `--prompt-file`
    /// names what the caller meant.
    pub fn resolve_headless_paths(&mut self, invocation_cwd: &Path) {
        for slot in [&mut self.output_dir, &mut self.prompt_file] {
            if let Some(path) = slot.as_mut() {
                *path = resolve_invocation_path(path, invocation_cwd);
            }
        }
    }

    /// Validate and enter the requested working directory before startup.
    ///
    /// Without `--workdir`, a deleted current directory maps to the startup
    /// guard message so the process can exit before the terminal is acquired.
    pub fn change_workdir(&self) -> Result<PathBuf> {
        if let Some(requested) = &self.cwd {
            let workdir = resolve_user_path(requested, user_home().as_deref())?;
            std::env::set_current_dir(&workdir).with_context(|| {
                format!(
                    "--workdir does not exist or is not a directory: {}",
                    workdir.display()
                )
            })?;
        }
        let workdir = std::env::current_dir().map_err(|source| {
            if source.kind() == io::ErrorKind::NotFound {
                anyhow::anyhow!(DELETED_CWD_ERROR)
            } else {
                anyhow::anyhow!("Error: Could not read current working directory: {source}")
            }
        })?;
        workdir_to_string(&workdir)?;
        Ok(workdir)
    }

    /// Whether the CLI should run in headless (programmatic) mode.
    pub fn is_headless(&self) -> bool {
        self.prompt.is_some() || self.prompt_file.is_some()
    }

    /// Resolve the prompt: `--prompt-file` content, `-p` flag value, or stdin
    /// if piped. An empty `--prompt ""` counts as absent, so it falls back to
    /// stdin. The error is the message a usage error reports.
    pub fn resolve_prompt(&self, stdin_prompt: Option<String>) -> Result<String, String> {
        if let Some(ref path) = self.prompt_file {
            // A bare `-p` before `--prompt-file` only selects headless mode.
            if matches!(&self.prompt, Some(Some(text)) if !text.is_empty()) {
                return Err("Pass either -p TEXT or --prompt-file".to_owned());
            }
            let content = crate::headless_prompt::read_prompt_file(path)?;
            // An empty file falls back to stdin, like an empty `--prompt`.
            return Ok(if content.is_empty() {
                stdin_prompt.unwrap_or_default()
            } else {
                content
            });
        }
        Ok(match &self.prompt {
            Some(Some(text)) if !text.is_empty() => text.clone(),
            _ => stdin_prompt.unwrap_or_default(),
        })
    }

    /// Validate `--add-dir` paths before the run starts: a path that is not a
    /// directory is a usage error, not a late session failure.
    pub fn validate_add_dirs(&self) -> Result<(), String> {
        for dir in &self.add_dir {
            if !crate::utils::paths::expand_tilde(dir).is_dir() {
                return Err(format!(
                    "--add-dir path does not exist or is not a directory: {}",
                    dir.display()
                ));
            }
        }
        Ok(())
    }

    /// Resolve the interactive initial prompt: the positional argument, or piped
    /// stdin when the positional is absent or empty, so `echo hi | vibe ""`
    /// still sends `hi`.
    pub fn interactive_initial_prompt(&self, stdin_prompt: Option<String>) -> Option<String> {
        match &self.initial_prompt {
            Some(text) if !text.is_empty() => Some(text.clone()),
            _ => stdin_prompt,
        }
    }

    /// The `--worktree` flag resolved into the wire `WorktreeInput` sent with
    /// `session/start`. `--worktree NAME` creates (or reuses) a worktree whose
    /// branch defaults to the name. Bare
    /// `--worktree` asks the server to name one from `auto_prompt` (the
    /// interactive initial prompt or the headless prompt), falling back to a
    /// random slug when there is none. An empty NAME means no worktree at all.
    pub fn worktree_request(&self, auto_prompt: Option<String>) -> Option<WorktreeInput> {
        match &self.worktree {
            Some(Some(name)) if !name.is_empty() => Some(WorktreeInput::Create {
                branch: name.clone(),
                name: name.clone(),
            }),
            Some(None) => Some(WorktreeInput::Auto {
                prompt: auto_prompt,
            }),
            Some(Some(_)) | None => None,
        }
    }

    /// Build headless options from the parsed CLI + stdin prompt. Agent and
    /// auto-approve go through `execution_control()` so `--smart-approve`
    /// resolves the agent in headless mode too. The error is the message a
    /// usage error reports.
    pub fn headless_options(
        &self,
        stdin_prompt: Option<String>,
    ) -> Result<HeadlessOptions, String> {
        let prompt = self.resolve_prompt(stdin_prompt)?;
        if prompt.is_empty() {
            return Err("No prompt provided for programmatic mode".to_owned());
        }
        let session = self.headless_session()?;
        let execution_control = self.execution_control();
        let worktree = self.worktree_request(Some(prompt.clone()));
        Ok(HeadlessOptions {
            session,
            output: self.output.clone(),
            max_turns: self.max_turns,
            max_price: self.max_price,
            max_tokens: self.max_tokens,
            time_limit: self.time_limit,
            output_dir: self.output_dir.clone(),
            agent: execution_control.agent,
            auto_approve: execution_control.auto_approve,
            enabled_tools: self.enabled_tools.clone(),
            disabled_tools: self.disabled_tools.clone(),
            // A worktree implies trust; an empty NAME requests no worktree.
            trust: self.trust || worktree.is_some(),
            add_dir: self.add_dir.clone(),
            worktree,
            agent_socket: self.agent_socket.clone(),
            prompt,
        })
    }

    /// The shared session flags for interactive mode: `--agent`,
    /// `--auto-approve` / `--yolo`, and `--smart-approve` (resolved via
    /// `execution_control()`), the tool filters, `--trust`, `--add-dir`, and
    /// the resolved `--worktree` request. Budget flags stay headless-only.
    pub fn interactive_agent_config(
        &self,
        cwd: Option<String>,
        worktree: Option<WorktreeInput>,
    ) -> Result<AgentConfig> {
        let execution_control = self.execution_control();
        Ok(AgentConfig {
            cwd,
            agent: execution_control.agent,
            auto_approve: execution_control.auto_approve,
            enabled_tools: self.enabled_tools.clone(),
            disabled_tools: self.disabled_tools.clone(),
            max_turns: None,
            max_price: None,
            max_session_tokens: None,
            headless: false,
            // A worktree implies trust.
            trust_workspace: self.trust || worktree.is_some(),
            workspace_roots: crate::utils::paths::resolve_add_dirs(&self.add_dir)?,
            worktree,
        })
    }

    /// Resolve the `--continue`/`--resume` flags into a startup resume intent.
    pub fn startup_resume(&self) -> StartupResume {
        if self.continue_session {
            return StartupResume::Continue;
        }
        match &self.resume {
            Some(Some(id)) => StartupResume::ById(id.clone()),
            Some(None) => StartupResume::Picker,
            None => StartupResume::None,
        }
    }

    /// Resolve `--continue`/`--resume` for a headless run, which has no
    /// picker: a bare `--resume` is a usage error.
    pub fn headless_session(&self) -> Result<HeadlessSession, String> {
        match self.startup_resume() {
            StartupResume::None => Ok(HeadlessSession::New),
            StartupResume::Continue => Ok(HeadlessSession::Continue),
            StartupResume::ById(id) => Ok(HeadlessSession::Resume(id)),
            StartupResume::Picker => {
                Err("--resume requires a session ID in programmatic mode".to_owned())
            }
        }
    }

    /// Resolve `--agent` / `--auto-approve` / `--smart-approve` into the
    /// wire-facing execution control. `--smart-approve` selects the
    /// `smart-approve` agent unless an explicit `--agent` wins.
    pub fn execution_control(&self) -> ExecutionControl {
        let agent = self
            .agent
            .clone()
            .or_else(|| self.smart_approve.then(|| "smart-approve".to_owned()));
        ExecutionControl {
            agent,
            auto_approve: self.auto_approve,
        }
    }
}
