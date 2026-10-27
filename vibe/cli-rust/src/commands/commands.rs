//! Built-in slash commands, hardcoded in the client.

use crate::completion_manager::CompletionEntry;
use crate::skill::Skill;

/// One `/`-prefixed alias group; `side_channel` marks the
/// commands that still run while the session is busy. The first alias is canonical.
pub struct Command {
    pub aliases: &'static [&'static str],
    pub description: &'static str,
    pub side_channel: bool,
    /// A side-channel command that still waits for a compaction, teleport, project request or agent install.
    pub waits_for_jobs: bool,
    /// Inline hint shown after `/command ` until an argument is typed; empty when none.
    pub arguments: &'static str,
}

const fn cmd(
    aliases: &'static [&'static str],
    description: &'static str,
    side_channel: bool,
) -> Command {
    Command {
        aliases,
        description,
        side_channel,
        waits_for_jobs: false,
        arguments: "",
    }
}

impl Command {
    const fn args(self, arguments: &'static str) -> Self {
        Self { arguments, ..self }
    }

    const fn after_jobs(self) -> Self {
        Self {
            waits_for_jobs: true,
            ..self
        }
    }
}

const COMMANDS: &[Command] = &[
    cmd(&["/help"], "Show help message", true),
    cmd(&["/config"], "Edit config settings", false),
    cmd(&["/model"], "Select active model", false),
    cmd(&["/thinking"], "Select thinking level", false),
    cmd(&["/reload"], "Reload configuration, agent instructions, and skills from disk", false),
    cmd(&["/clear", "/new"], "Start a new conversation. Optionally pass a prompt to seed it.", false).args("[prompt]"),
    cmd(&["/copy"], "Copy the last agent message to the clipboard", true),
    cmd(&["/log"], "Show path to current interaction log file", true),
    cmd(&["/log-level"], "Change the log level for this session or persist it to config.toml.", true).after_jobs(),
    cmd(&["/debug"], "Toggle debug console", true),
    cmd(
        &["/stress"],
        "Repeat renderable history entries to load-test rendering. Optional arg: entries (default 100).",
        true,
    )
    .args("[entries]"),
    cmd(&["/compact"], "Compact conversation history by summarizing. Optionally pass instructions to guide the summary", false).args("[instructions]"),
    cmd(&["/exit", ":q", ":quit", "exit", "quit"], "Exit the application", true),
    cmd(&["/status"], "Display agent statistics and model and provider details", true),
    cmd(&["/whoami"], "Display the Mistral signed-in user, workspace, and plan", true),
    cmd(&["/proxy-setup"], "Configure proxy and SSL certificate settings", true).after_jobs(),
    cmd(&["/resume", "/continue"], "Browse, resume, or delete saved sessions", false),
    cmd(&["/rename"], "Rename the current session", true).args("<title>"),
    cmd(&["/mcp", "/connectors"], "Display available MCP servers and connectors. Pass a name to list tools; subcommands: add <url> [--transport http|streamable-http], status, login <alias>, logout <alias>", false)
        .args("[name] | add <url> | status | login <alias> | logout <alias>"),
    cmd(&["/plugins"], "Display the plugins this session is running", false),
    cmd(&["/reload-plugins"], "Re-pin this session's plugins and report what changed", false),
    cmd(&["/voice"], "Configure voice settings", true).after_jobs(),
    cmd(&["/leanstall"], "Install the Lean 4 agent (leanstral)", false),
    cmd(&["/unleanstall"], "Uninstall the Lean 4 agent", false),
    cmd(&["/rewind"], "Rewind to a previous message (or press Esc twice)", false),
    cmd(&["/branch", "/fork"], "Fork the current conversation into a new resumable session, leaving this session unchanged. Resume the copy with `vibe --resume <id>`.", false),
    cmd(&["/retry"], "Continue an interrupted model response; optionally pass additional instructions", false).args("[instructions]"),
    cmd(&["/loop"], "Schedule a recurring prompt: /loop [schedule] [prompt]", false).args("[schedule] [prompt]"),
    cmd(&["/data-retention"], "Show data retention information", true),
    cmd(&["/theme"], "Select theme", true).after_jobs(),
    cmd(&["/teleport"], "Teleport session to Vibe Code Web", false),
    cmd(
        &["/remote-project"],
        "Select the Vibe Code Web project for this repository",
        true,
    )
    .after_jobs(),
];

const PASTE_IMAGE: Command = cmd(
    &["/paste-image"],
    "Paste an image from the OS clipboard into the prompt",
    true,
);

/// `/paste-image` is macOS-only; the e2e replay hides it so goldens match on every OS.
fn available() -> impl Iterator<Item = &'static Command> {
    let paste_image = crate::paste_image::is_supported() && !crate::utils::is_replaying();
    COMMANDS.iter().chain(paste_image.then_some(&PASTE_IMAGE))
}

/// Whether a built-in command with this canonical alias is registered.
pub fn has_command(label: &str) -> bool {
    available().any(|command| command.aliases[0] == label)
}

/// Arguments hint shown in slash mode after `command ` until the first argument character is typed; `prefix` is the input-mode prefix.
pub fn argument_hint(prefix: Option<char>, body: &str) -> Option<&'static str> {
    let word = body.strip_suffix(' ').filter(|_| prefix == Some('/'))?;
    available()
        .find(|command| {
            command.aliases.iter().any(|alias| {
                alias
                    .strip_prefix('/')
                    .is_some_and(|alias| alias.eq_ignore_ascii_case(word))
            })
        })
        .map(|command| command.arguments)
        .filter(|arguments| !arguments.is_empty())
}

/// Whether the command runs while a turn is generating.
pub fn is_side_channel(label: &str) -> bool {
    available().any(|command| command.aliases.contains(&label) && command.side_channel)
}

/// Whether a side-channel command still waits for a compaction, teleport, project request or agent install.
pub fn waits_for_jobs(label: &str) -> bool {
    available().any(|command| command.aliases.contains(&label) && command.waits_for_jobs)
}

/// Resolve the slash-command word, returning the canonical alias.
pub fn parse(input: &str) -> Option<&'static str> {
    let mut words = input.split_whitespace();
    let word = words.next()?;
    // Bare aliases (e.g. `exit`) match only as the whole input, else a message
    // starting with one would be swallowed.
    if !word.starts_with('/') && words.next().is_some() {
        return None;
    }
    if word.eq_ignore_ascii_case("/clean") {
        return Some("/clear");
    }
    available().find_map(|command| {
        command
            .aliases
            .iter()
            .any(|a| a.eq_ignore_ascii_case(word))
            .then(|| command.aliases[0])
    })
}

/// Built-in commands only (no skills), one entry per command group, for `/help`.
/// Each label joins all aliases in backticks, canonical first.
pub fn builtin() -> Vec<(String, String)> {
    available()
        .map(|command| {
            let labels = command
                .aliases
                .iter()
                .map(|a| format!("`{a}`"))
                .collect::<Vec<_>>()
                .join(", ");
            (labels, command.description.to_owned())
        })
        .collect()
}

/// The full popup entry list, expanding aliases into individual entries,
/// merging with skills as `/name`, sorted by name then description. A skill
/// named like a built-in is left out, since typing its name runs the built-in.
pub fn entries(skills: &[Skill]) -> Vec<CompletionEntry> {
    let mut out: Vec<CompletionEntry> = available()
        .flat_map(|command| {
            command
                .aliases
                .iter()
                .filter(|a| a.starts_with('/'))
                .map(|a| CompletionEntry {
                    label: (*a).to_owned(),
                    description: command.description.to_owned(),
                    origin: None,
                })
                .collect::<Vec<_>>()
        })
        .collect();
    out.extend(skills.iter().filter_map(|skill| {
        let label = format!("/{}", skill.name);
        parse(&label).is_none().then(|| CompletionEntry {
            label,
            description: skill.description.clone(),
            origin: Some(skill.origin),
        })
    }));
    out.sort_by(|a, b| {
        a.label
            .cmp(&b.label)
            .then_with(|| a.description.cmp(&b.description))
    });
    out
}
