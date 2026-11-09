//! Copy through tmux's own clipboard forwarding (`load-buffer -w`), which needs no DCS passthrough.

use crate::tmux::run as tmux;

/// Load `text` into a tmux buffer and forward it to the session's most recently active client.
pub(super) fn copy(text: &str) -> bool {
    let Ok(pane) = std::env::var("TMUX_PANE") else {
        return false;
    };
    target_client(&pane)
        .is_some_and(|client| super::pipe("tmux", &["load-buffer", "-w", "-t", &client, "-"], text))
}

/// The pane session's most active client, if tmux forwards copies to its terminal (`Ms` capability).
fn target_client(pane: &str) -> Option<String> {
    let output = tmux(&[
        "show-options",
        "-gv",
        "set-clipboard",
        ";",
        "list-clients",
        "-t",
        pane,
        "-F",
        "#{client_activity} #{client_name}",
    ])?;
    let (clipboard, clients) = output.split_once('\n')?;
    if clipboard.trim() == "off" {
        return None;
    }
    let client = clients
        .lines()
        .filter_map(|line| {
            let (activity, name) = line.split_once(' ')?;
            Some((activity.parse::<u64>().ok()?, name))
        })
        .filter(|(_, name)| !name.is_empty())
        .max_by_key(|(activity, _)| *activity)?
        .1
        .to_owned();
    let terminal = tmux(&["show-messages", "-T", "-t", &client])?;
    terminal
        .lines()
        .any(|line| {
            line.split_once("Ms: (string) ")
                .is_some_and(|(_, sequence)| !sequence.trim().is_empty())
        })
        .then_some(client)
}
