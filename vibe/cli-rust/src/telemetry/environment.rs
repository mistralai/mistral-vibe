//! The `environment` object on `vibe.startup`: terminal, multiplexer, remote host, shell and capabilities.

use serde_json::{json, Value};

use crate::terminal_capabilities::Capabilities;
use crate::terminal_detect::{is_ssh, multiplexer, Env, EnvSource};

const CONTAINER_MARKERS: [&str; 2] = ["/.dockerenv", "/run/.containerenv"];

/// The environment of this process, read once the terminal probe and tmux query have run.
pub fn properties() -> Value {
    let probed = crate::terminal_capabilities::probed();
    let reported = probed.version.clone().or_else(crate::tmux::client_termtype);
    let in_container = CONTAINER_MARKERS
        .iter()
        .any(|path| std::path::Path::new(path).exists());
    build(
        &Env,
        &probed,
        reported.as_deref(),
        crossterm::terminal::size().ok(),
        in_container,
    )
}

/// `reported` is the terminal's own XTVERSION text; `size` is `(columns, rows)`.
pub fn build(
    env: &dyn EnvSource,
    probed: &Capabilities,
    reported: Option<&str>,
    size: Option<(u16, u16)>,
    in_container: bool,
) -> Value {
    let (terminal_name, terminal_version) = reported.map(split_version).unwrap_or_default();
    json!({
        "term_program": env.get("TERM_PROGRAM").and_then(|value| token(&value)),
        "term": env.get("TERM").and_then(|value| token(&value)),
        "colorterm": env.get("COLORTERM").and_then(|value| token(&value)),
        "terminal_name": terminal_name,
        "terminal_version": terminal_version,
        "terminal_columns": size.map(|(columns, _)| columns),
        "terminal_rows": size.map(|(_, rows)| rows),
        "multiplexer": multiplexer(env).unwrap_or("none"),
        "is_ssh": is_ssh(env),
        "is_wsl": any_present(env, &["WSL_DISTRO_NAME", "WSL_INTEROP"]),
        "is_container": in_container || any_present(env, &["REMOTE_CONTAINERS", "CODESPACES", "container"]),
        "login_shell": env.get("SHELL").and_then(|path| shell_name(&path)),
        "is_no_color": env.present("NO_COLOR"),
        "is_utf8": is_utf8(env),
        "probe_status": probed.status,
        "has_mouse_pixel_reports": probed.mouse_pixel_reports,
    })
}

/// The shell's file name without `.exe`, such as `zsh` for `/bin/zsh`.
pub fn shell_name(path: &str) -> Option<String> {
    let name = path.rsplit(['/', '\\']).next()?;
    token(name.strip_suffix(".exe").unwrap_or(name))
}

/// The locale `LC_ALL`, `LC_CTYPE` or `LANG` selects is UTF-8; `None` when none is set.
pub fn is_utf8(env: &dyn EnvSource) -> Option<bool> {
    let locale = ["LC_ALL", "LC_CTYPE", "LANG"]
        .iter()
        .find_map(|name| env.get(name).filter(|value| !value.is_empty()))?
        .to_lowercase();
    Some(locale.contains("utf-8") || locale.contains("utf8"))
}

/// An XTVERSION text as name and version: `iTerm2 3.5.0` and `kitty(0.35.2)` both split.
pub fn split_version(text: &str) -> (Option<String>, Option<String>) {
    let (name, version) = text.split_once([' ', '(']).unwrap_or((text, ""));
    (token(name), token(version.trim_end_matches(')')))
}

/// `value` lowercased, kept to a short run of `[a-z0-9._+-]` so no free text or path leaves the machine.
pub fn token(value: &str) -> Option<String> {
    let token: String = value
        .trim()
        .to_lowercase()
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '+' | '-'))
        .take(40)
        .collect();
    (!token.is_empty()).then_some(token)
}

fn any_present(env: &dyn EnvSource, names: &[&str]) -> bool {
    names.iter().any(|name| env.present(name))
}
