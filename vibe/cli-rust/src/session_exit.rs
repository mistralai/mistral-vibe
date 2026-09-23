//! Post-exit summary and failure line (Python `cli/session_exit.py`).

use std::io::IsTerminal;
use std::io::Write;
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};

use crate::app::App;
use crate::resume_picker;
use crate::server::{method, Client, TokenUsage};

/// Bound on the quit-time `session/log/read`: Python answers from cached state,
/// so a hung server must not stall the exit; a timeout just drops the summary.
const EXIT_READ_TIMEOUT: Duration = Duration::from_millis(500);

/// Python `SessionExitSummary`: the persisted session and the usage since attach.
pub struct SessionExitSummary {
    pub session_id: Option<String>,
    pub usage: TokenUsage,
}

/// The `[bold dark_orange]` span on a 256-color-or-better terminal.
const ORANGE_256: &str = "\x1b[1;38;5;208m";
/// The same span downgraded by rich on a 16-color terminal: dark_orange becomes bright red.
const ORANGE_16: &str = "\x1b[1;91m";
/// `[red]`, which rich emits unchanged at every color depth.
const RED: &str = "\x1b[31m";
const RESET: &str = "\x1b[0m";

/// Token usage the app currently projects (Python `_current_usage`).
pub fn current_usage(app: &App) -> TokenUsage {
    TokenUsage {
        input_tokens: app.session.stats.session_prompt_tokens,
        output_tokens: app.session.stats.session_completion_tokens,
    }
}

/// Python `usage_since_baseline`: per-component clamp at zero.
pub fn usage_since_baseline(current: TokenUsage, baseline: TokenUsage) -> TokenUsage {
    TokenUsage {
        input_tokens: current.input_tokens.saturating_sub(baseline.input_tokens),
        output_tokens: current.output_tokens.saturating_sub(baseline.output_tokens),
    }
}

/// Python `format_session_usage` (plain labels, thousands separators).
pub fn format_session_usage(usage: TokenUsage) -> String {
    format!(
        "Total tokens used this session: input={} output={} (total={})",
        thousands(usage.input_tokens),
        thousands(usage.output_tokens),
        thousands(usage.input_tokens + usage.output_tokens),
    )
}

/// Python's `:,` integer format: digits grouped in threes by commas.
fn thousands(value: u64) -> String {
    let digits = value.to_string();
    let mut grouped = String::with_capacity(digits.len() + digits.len() / 3);
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            grouped.push(',');
        }
        grouped.push(digit);
    }
    grouped
}

/// Python `print_session_resume_message` as one block: nothing at all without a
/// persisted session id, otherwise the usage line and the two resume lines.
pub fn session_resume_message(
    summary: Option<&SessionExitSummary>,
    orange: Option<&str>,
) -> String {
    let Some(summary) = summary else {
        return String::new();
    };
    let Some(session_id) = summary.session_id.as_deref() else {
        return String::new();
    };
    let mut text = format!("\n{}\n\n", format_session_usage(summary.usage));
    match orange {
        Some(code) => {
            text.push_str(&format!(
                "To continue this session, run: {code}vibe --continue{RESET}\n"
            ));
            text.push_str(&format!(
                "Or: {code}vibe --resume {}{RESET}\n",
                resume_picker::short_id(session_id)
            ));
        }
        None => {
            text.push_str("To continue this session, run: vibe --continue\n");
            text.push_str(&format!(
                "Or: vibe --resume {}\n",
                resume_picker::short_id(session_id)
            ));
        }
    }
    text
}

/// Print the resume block on the restored terminal (Python prints it only
/// after the TUI exits, so plain stdout reaches the TTY).
pub fn print_session_resume_message(summary: Option<&SessionExitSummary>) {
    let text = session_resume_message(summary, orange_span(std::io::stdout().is_terminal()));
    if text.is_empty() {
        return;
    }
    let mut stdout = std::io::stdout().lock();
    let _ = stdout.write_all(text.as_bytes());
    let _ = stdout.flush();
}

/// Python's fatal `AppServerResponseError` line: red `Error:` prefix on stdout
/// (rich's `Console` defaults to `sys.stdout`).
pub fn print_error(message: &str) {
    // Under NO_COLOR rich drops the red entirely, unlike the kept-bold orange.
    let red = if no_color() {
        None
    } else {
        color_span(std::io::stdout().is_terminal(), RED, RED)
    };
    let _ = writeln!(
        std::io::stdout(),
        "{}",
        match red {
            Some(code) => format!("{code}Error:{RESET} {message}"),
            None => format!("Error: {message}"),
        }
    );
}

/// Python `AppServerSession.exit_summary`: the id only while the session log is
/// enabled and persisted; the usage is the delta since the session attached.
pub async fn exit_summary(app: &App, client: &Arc<Client>) -> SessionExitSummary {
    let current = current_usage(app);
    // A quit before `ready` never attached, so nothing was used since attach.
    let usage = usage_since_baseline(current, app.session.usage_baseline.unwrap_or(current));
    let Some(session_id) = app.session.session_id.clone() else {
        return SessionExitSummary {
            session_id: None,
            usage,
        };
    };
    let persisted = tokio::time::timeout(
        EXIT_READ_TIMEOUT,
        client.request(method::SESSION_LOG_READ, json!({"sessionId": session_id})),
    )
    .await
    .ok()
    .and_then(|response| response.ok())
    .filter(|log| {
        log.pointer("/log/enabled") == Some(&Value::Bool(true))
            && log.pointer("/log/persisted") == Some(&Value::Bool(true))
    });
    SessionExitSummary {
        session_id: persisted.map(|_| session_id),
        usage,
    }
}

/// The `[bold dark_orange]` prefix rich emits for a stdout with this tty bit and
/// environment, or `None` when rich would print the resume lines plain.
pub fn orange_span(is_tty: bool) -> Option<&'static str> {
    if !is_tty || dumb_term() {
        return None;
    }
    if no_color() {
        // rich keeps the bold attribute and drops the color.
        return Some("\x1b[1m");
    }
    color_span(is_tty, ORANGE_256, ORANGE_16)
}

/// rich's NO_COLOR check: any non-empty value turns colors off.
fn no_color() -> bool {
    std::env::var("NO_COLOR")
        .map(|v| !v.is_empty())
        .unwrap_or(false)
}

/// rich's terminal detection (`Console._detect_color_system`): no SGR off-TTY
/// or on a dumb terminal, else the depth-appropriate escape.
fn color_span(is_tty: bool, deep: &'static str, shallow: &'static str) -> Option<&'static str> {
    if !is_tty || dumb_term() {
        return None;
    }
    let colorterm = std::env::var("COLORTERM")
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    if colorterm == "truecolor" || colorterm == "24bit" {
        return Some(deep);
    }
    let term = std::env::var("TERM")
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    let colors = term.rsplit_once('-').map(|(_, tail)| tail).unwrap_or(&term);
    Some(match colors {
        "256color" | "kitty" => deep,
        _ => shallow,
    })
}

/// rich's dumb-terminal gate: `dumb` and `unknown` TERM values are colorless.
fn dumb_term() -> bool {
    let term = std::env::var("TERM")
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    term == "dumb" || term == "unknown"
}
