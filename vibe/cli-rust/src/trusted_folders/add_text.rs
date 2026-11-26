//! Pure add-view text: the typed path's status, and what trusting the highlighted folder covers.

use super::add::Add;
use super::rows::label;
use crate::hints::{self, action, key, Hint};
use crate::server::TrustFolderCandidate;

/// How a line of the add view reads.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tone {
    Plain,
    Muted,
    Warning,
    Error,
}

/// Above the candidates: why the typed path is not offered as is.
pub fn status(add: &Add) -> Option<(Tone, String)> {
    if let Some(error) = add.error.as_deref() {
        return Some((Tone::Error, error.to_owned()));
    }
    let Some(inspection) = add.inspection.as_ref() else {
        return Some((Tone::Muted, "Checking…".to_owned()));
    };
    let text = match inspection.typed_problem.as_deref()? {
        "not_directory" => "This path is a file. Its folder is offered instead.",
        _ => "No folder at this path. The closest existing folder is offered instead.",
    };
    Some((Tone::Error, text.to_owned()))
}

/// The candidate's row: its path, and its current state when it has one.
pub fn row(candidate: &TrustFolderCandidate) -> (String, Option<&'static str>) {
    let note = match (candidate.problem.as_deref(), candidate.stored.as_deref()) {
        (Some("home"), _) => Some("home folder"),
        (Some("scratch"), _) => Some("scratch folder"),
        (Some(_), _) => Some("filesystem root"),
        (None, Some("trusted")) => Some("already trusted"),
        (None, Some(_)) => Some("untrusted"),
        (None, None) if candidate.trusted_by.is_some() => Some("trusted through a parent"),
        (None, None) => None,
    };
    (label(&candidate.path), note)
}

/// Under the candidates: what trusting the highlighted one would do.
pub fn details(add: &Add) -> Vec<(Tone, String)> {
    let Some(candidate) = add.highlighted() else {
        return Vec::new();
    };
    let path = label(&candidate.path);
    if let Some(problem) = candidate.problem.as_deref() {
        let why = match problem {
            "home" => "Your home folder cannot be trusted: it would trust every project in it.",
            "scratch" => "A scratch folder cannot be trusted: a session with no project wrote it.",
            _ => "A filesystem root cannot be trusted: it would trust every folder on the drive.",
        };
        return vec![(Tone::Error, why.to_owned())];
    }
    if candidate.stored.as_deref() == Some("trusted") {
        return vec![(Tone::Muted, format!("{path} is already trusted."))];
    }
    let mut out = vec![(
        Tone::Warning,
        format!("Trusting {path} trusts every folder inside it too, unless it is explicitly untrusted: their AGENTS.md, .vibe/ and .agents/ config loads the next time Vibe starts."),
    )];
    if let Some(root) = candidate.trusted_by.as_deref() {
        let root = label(root);
        out.push((Tone::Muted, format!("Already trusted through {root}; adding it keeps it trusted if that entry is removed.")));
    }
    if candidate.untrusted_inside_count > 0 {
        let shown: Vec<String> = candidate
            .untrusted_inside
            .iter()
            .map(|path| label(path))
            .collect();
        let more = candidate.untrusted_inside_count - shown.len();
        let more = if more > 0 {
            format!(" and {more} more")
        } else {
            String::new()
        };
        out.push((
            Tone::Muted,
            format!("Stays untrusted: {}{more}", shown.join(", ")),
        ));
    }
    out
}

pub fn help(add: &Add) -> Vec<Hint> {
    let mut help = Vec::new();
    if add.candidates().len() > 1 {
        help.push((key::ARROWS, action::NAVIGATE));
    }
    if add.addable() {
        help.push((key::ENTER, action::TRUST));
    }
    if add.highlighted().is_some() {
        help.push((key::TAB, action::COMPLETE));
    }
    help.push(hints::BACK);
    help
}
