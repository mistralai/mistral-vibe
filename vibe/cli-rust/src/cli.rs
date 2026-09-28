//! Typed CLI arguments.

use std::{
    fmt, io,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result};
use clap::{ArgAction, Parser};

use crate::server::AgentConfig;

const DELETED_CWD_ERROR: &str = "Error: Current working directory no longer exists.\n\n\
The directory you started vibe from has been deleted. Please change to an existing directory \
and try again, or use --workdir to specify a working directory.";

/// Output format for headless mode, mirroring Python `OutputFormat`.
#[derive(Clone, Debug, PartialEq, Eq, Default, clap::ValueEnum)]
#[value(rename_all = "lowercase")]
pub enum OutputFormat {
    #[default]
    Text,
    Json,
    Streaming,
}

#[derive(Parser, Debug)]
#[command(name = "vibe", bin_name = "vibe", version = env!("CARGO_PKG_VERSION"), disable_version_flag = true, about = "Rust TUI for Vibe (PoC)")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<CliCommand>,

    /// Show version and exit.
    #[arg(short = 'v', long = "version", action = ArgAction::Version)]
    _version: Option<bool>,

    /// Change to this directory before running.
    #[arg(long = "workdir", env = "VIBE_CWD")]
    pub cwd: Option<PathBuf>,

    /// Continue from the most recent saved session.
    #[arg(short = 'c', long = "continue", action = ArgAction::SetTrue, conflicts_with_all = ["resume", "prompt"])]
    pub continue_session: bool,

    /// Resume a session. Without SESSION_ID, shows an interactive picker.
    #[arg(long = "resume", num_args = 0..=1, conflicts_with_all = ["continue_session", "prompt"])]
    pub resume: Option<Option<String>>,

    /// Initial prompt for the interactive session, or the prompt in headless mode.
    #[arg(num_args = 0..=1)]
    pub initial_prompt: Option<String>,

    /// Run in headless (programmatic) mode: send prompt, output response, exit.
    #[arg(short = 'p', long = "prompt", num_args = 0..=1)]
    pub prompt: Option<Option<String>>,

    /// Output format for headless mode.
    #[arg(long = "output", value_enum, default_value = "text")]
    pub output: OutputFormat,

    /// Maximum number of assistant turns (headless mode only).
    #[arg(long = "max-turns")]
    pub max_turns: Option<u32>,

    /// Maximum cost in dollars (headless mode only).
    #[arg(long = "max-price")]
    pub max_price: Option<f64>,

    /// Maximum total prompt + completion tokens (headless mode only).
    #[arg(long = "max-tokens")]
    pub max_tokens: Option<u64>,

    /// Agent to use (builtin: ask, plan, accept-edits, smart-approve,
    /// auto-approve, or custom from ~/.vibe/agents/NAME.toml). Defaults to the
    /// 'default_agent' config setting.
    #[arg(long = "agent", value_name = "NAME")]
    pub agent: Option<String>,

    /// Approves all tool calls without prompting for the selected agent.
    #[arg(long = "auto-approve", visible_alias = "yolo", action = ArgAction::SetTrue)]
    pub auto_approve: bool,

    /// Classify each tool call and auto-run the safe ones, prompting only for
    /// risky ones.
    #[arg(long = "smart-approve", action = ArgAction::SetTrue)]
    pub smart_approve: bool,

    /// Enable specific tools (headless: disables all others). Repeat the flag.
    #[arg(long = "enabled-tools")]
    pub enabled_tools: Vec<String>,

    /// Disable specific tools after --enabled-tools filtering. Repeat the flag.
    #[arg(long = "disabled-tools")]
    pub disabled_tools: Vec<String>,

    /// Trust the working directory for this invocation only.
    #[arg(long = "trust", action = ArgAction::SetTrue)]
    pub trust: bool,

    /// Additional working directory for file access. Repeat the flag.
    #[arg(long = "add-dir")]
    pub add_dir: Vec<PathBuf>,

    /// Feature flag for teleport (hidden).
    #[arg(long = "teleport", action = ArgAction::SetTrue, hide = true, conflicts_with = "prompt")]
    pub teleport: bool,
}

#[derive(Debug, clap::Subcommand)]
pub enum CliCommand {
    /// Manage MCP server configuration without starting a session.
    Mcp {
        #[command(subcommand)]
        command: crate::mcp_command::McpCommand,
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

/// The startup session intent parsed from the CLI flags, mirroring the Python
/// `_session_intent`: `--continue`, `--resume <id>`, `--resume` (picker), or none.
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

/// Parsed headless-mode options, mirroring the Python `_run_programmatic_mode` args.
#[derive(Clone, Debug)]
pub struct HeadlessOptions {
    pub prompt: String,
    pub output: OutputFormat,
    pub max_turns: Option<u32>,
    pub max_price: Option<f64>,
    pub max_tokens: Option<u64>,
    pub agent: Option<String>,
    pub auto_approve: bool,
    pub enabled_tools: Vec<String>,
    pub disabled_tools: Vec<String>,
    pub trust: bool,
    pub add_dir: Vec<PathBuf>,
}

/// The execution-control config resolved from the CLI flags, sent to the
/// app-server in `agentConfig`. Mirrors Python's post-parse resolution in
/// `cli/entrypoint.py` minus the always-on unified harness, except that
/// `--auto-approve`/`--yolo` is a session-wide bypass and never swaps the
/// agent (deliberate divergence from Python's `_agent_selection`, which maps
/// a bare `--yolo` to the `auto-approve` agent).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ExecutionControl {
    /// Resolved agent name; `None` lets the server use `default_agent`.
    pub agent: Option<String>,
    /// Approve every tool call (`--auto-approve` / `--yolo`).
    pub auto_approve: bool,
}

impl Cli {
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
        self.prompt.is_some()
    }

    /// Resolve the prompt: `-p` flag value, or stdin if piped, or the positional.
    /// Mirrors Python `args.prompt or stdin_prompt` and `args.initial_prompt`:
    /// an empty `--prompt ""` is falsy, so it falls back to stdin.
    pub fn resolve_prompt(&self, stdin_prompt: Option<String>) -> Option<String> {
        match &self.prompt {
            Some(text) if text.as_deref().is_some_and(|t| !t.is_empty()) => text.clone(),
            Some(_) => stdin_prompt,
            None => None,
        }
    }

    /// Resolve the interactive initial prompt: the positional argument, or piped
    /// stdin when the positional is absent or empty. Mirrors Python
    /// `initial_prompt or stdin_prompt`, where an empty positional (`vibe ""`) is
    /// falsy and falls back to stdin, so `echo hi | vibe ""` still sends `hi`.
    pub fn interactive_initial_prompt(&self, stdin_prompt: Option<String>) -> Option<String> {
        match &self.initial_prompt {
            Some(text) if !text.is_empty() => Some(text.clone()),
            _ => stdin_prompt,
        }
    }

    /// Build headless options from the parsed CLI + stdin prompt. Agent and
    /// auto-approve go through `execution_control()` so `--smart-approve`
    /// resolves the agent in headless mode too, as in Python's post-parse
    /// resolution.
    pub fn headless_options(&self, stdin_prompt: Option<String>) -> Option<HeadlessOptions> {
        let prompt = self.resolve_prompt(stdin_prompt)?;
        let execution_control = self.execution_control();
        Some(HeadlessOptions {
            prompt,
            output: self.output.clone(),
            max_turns: self.max_turns,
            max_price: self.max_price,
            max_tokens: self.max_tokens,
            agent: execution_control.agent,
            auto_approve: execution_control.auto_approve,
            enabled_tools: self.enabled_tools.clone(),
            disabled_tools: self.disabled_tools.clone(),
            trust: self.trust,
            add_dir: self.add_dir.clone(),
        })
    }

    /// The shared session flags for interactive mode, mirroring the Python
    /// interactive `SessionOptions` (cli.py): `--agent`, `--auto-approve` /
    /// `--yolo`, and `--smart-approve` (resolved via `execution_control()`),
    /// the tool filters, `--trust`, and `--add-dir`. Budget flags stay
    /// headless-only, as in Python.
    pub fn interactive_agent_config(&self, cwd: Option<String>) -> Result<AgentConfig> {
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
            trust_workspace: self.trust,
            workspace_roots: crate::utils::paths::resolve_add_dirs(&self.add_dir)?,
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
