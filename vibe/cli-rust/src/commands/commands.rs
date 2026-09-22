//! Built-in slash commands, hardcoded to mirror Python's `vibe/cli/commands.py`.

/// One `/`-prefixed alias group (Python's `Command`); `side_channel` marks the
/// commands that still run while the session is busy. The first alias is canonical.
pub struct Command {
    pub aliases: &'static [&'static str],
    pub description: &'static str,
    pub side_channel: bool,
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
    }
}

const COMMANDS: &[Command] = &[
    cmd(&["/help"], "Show help message", true),
    cmd(&["/config"], "Edit config settings", false),
    cmd(&["/model"], "Select active model", false),
    cmd(&["/thinking"], "Select thinking level", false),
    cmd(&["/reload"], "Reload configuration, agent instructions, and skills from disk", false),
    cmd(&["/clear", "/new"], "Start a new conversation. Optionally pass a prompt to seed it.", false),
    cmd(&["/copy"], "Copy the last agent message to the clipboard", true),
    cmd(&["/log"], "Show path to current interaction log file", true),
    cmd(&["/log-level"], "Change the log level for this session or persist it to config.toml.", false),
    cmd(&["/debug"], "Toggle debug console", true),
    cmd(
        &["/stress"],
        "Repeat renderable history entries to load-test rendering. Optional arg: entries (default 100).",
        true,
    ),
    cmd(&["/compact"], "Compact conversation history by summarizing. Optionally pass instructions to guide the summary", false),
    cmd(&["/exit", ":q", ":quit", "exit", "quit"], "Exit the application", true),
    cmd(&["/status"], "Display agent statistics", true),
    cmd(&["/whoami"], "Display the Mistral signed-in user, workspace, and plan", true),
    cmd(&["/proxy-setup"], "Configure proxy and SSL certificate settings", false),
    cmd(&["/resume", "/continue"], "Browse, resume, or delete saved sessions", false),
    cmd(&["/rename"], "Rename the current session", true),
    cmd(&["/mcp", "/connectors"], "Display available MCP servers and connectors. Pass a name to list tools; subcommands: add <url> [--transport http|streamable-http], status, login <alias>, logout <alias>", false),
    cmd(&["/voice"], "Configure voice settings", false),
    cmd(&["/leanstall"], "Install the Lean 4 agent (leanstral)", false),
    cmd(&["/unleanstall"], "Uninstall the Lean 4 agent", false),
    cmd(&["/rewind"], "Rewind to a previous message (or press Esc twice)", false),
    cmd(&["/retry"], "Continue an interrupted model response; optionally pass additional instructions", false),
    cmd(&["/loop"], "Schedule a recurring prompt. Use `/loop <interval> <prompt>`, `/loop list`, or `/loop cancel <id|all>`", false),
    cmd(&["/data-retention"], "Show data retention information", true),
    cmd(&["/theme"], "Select theme", false),
    cmd(&["/teleport"], "Teleport session to Vibe Code Web", false),
    cmd(
        &["/remote-project"],
        "Select the Vibe Code Web project for this repository",
        false,
    ),
];

/// `/paste-image`: only available on macOS, matching Python's `is_available`.
#[cfg(target_os = "macos")]
const PASTE_IMAGE: Option<Command> = Some(cmd(
    &["/paste-image"],
    "Paste an image from the OS clipboard into the prompt",
    true,
));
#[cfg(not(target_os = "macos"))]
const PASTE_IMAGE: Option<Command> = None;

fn available() -> impl Iterator<Item = &'static Command> {
    COMMANDS.iter().chain(PASTE_IMAGE.iter())
}

/// Whether the command runs while a turn is generating (Python `side_channel=True`).
pub fn is_side_channel(label: &str) -> bool {
    available().any(|command| command.aliases.contains(&label) && command.side_channel)
}

/// Resolve the slash-command word, returning the canonical alias.
pub fn parse(input: &str) -> Option<&'static str> {
    let mut words = input.split_whitespace();
    let word = words.next()?;
    // Bare aliases (e.g. `exit`) match only as the whole input, else a message
    // starting with one would be swallowed (Python `parse_command`).
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
/// merging with skills as `/name`, sorted like Python's `_get_slash_entries`.
pub fn entries(skills: &[(String, String)]) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = available()
        .flat_map(|command| {
            command
                .aliases
                .iter()
                .filter(|a| a.starts_with('/'))
                .map(|a| ((*a).to_owned(), command.description.to_owned()))
                .collect::<Vec<_>>()
        })
        .collect();
    out.extend(
        skills
            .iter()
            .map(|(name, desc)| (format!("/{name}"), desc.clone())),
    );
    out.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
    out
}
