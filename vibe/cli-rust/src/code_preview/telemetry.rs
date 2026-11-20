//! `vibe.code_preview.*`: `opened` when a popup is first painted, one `done` per preview attempt.

use std::collections::BTreeMap;

use serde_json::{json, Value};

use super::load::Failure;
use super::target::{Kind, Location};
use crate::app::App;
use crate::clock::UiInstant;
use crate::selection::RegionId;
use crate::telemetry::{bmap, event, record};

/// The `vibe.user_copied_text` source of a copy out of the preview.
pub const COPY_SOURCE: &str = "code_preview";

/// What closed an open preview.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Close {
    PointerLeft,
    Escape,
    Key,
    ClickOutside,
    FocusChanged,
    LinkMoved,
    LinkChanged,
}

impl Close {
    fn name(self) -> &'static str {
        match self {
            Self::PointerLeft => "pointer_left",
            Self::Escape => "escape",
            Self::Key => "key",
            Self::ClickOutside => "click_outside",
            Self::FocusChanged => "focus_changed",
            Self::LinkMoved => "link_moved",
            Self::LinkChanged => "link_changed",
        }
    }
}

/// The preview attempt `done` reports on.
pub struct Visit {
    link: BTreeMap<String, Value>,
    /// The first paint of the popup, when it has been seen.
    rendered_at: Option<UiInstant>,
    pub has_scrolled: bool,
    pub has_copied: bool,
}

enum End {
    Failed(Failure),
    Closed(Close),
}

/// The delay elapsed on a link to `location`: a preview attempt starts.
pub fn started(app: &mut App, location: &Location) {
    app.code_preview.visit = Some(Visit {
        link: link_properties(location),
        rendered_at: None,
        has_scrolled: false,
        has_copied: false,
    });
}

/// The popup was painted; its first paint records `opened`.
pub fn rendered(app: &mut App) {
    let Some(visit) = app.code_preview.visit.as_mut() else {
        return;
    };
    if visit.rendered_at.is_some() {
        return;
    }
    visit.rendered_at = Some(crate::clock::now());
    let link = visit.link.clone();
    record(app, event::CODE_PREVIEW_OPENED, link);
}

/// The file could not be read, which ends the attempt unseen.
pub fn failed(app: &mut App, failure: Failure) {
    finish(app, End::Failed(failure));
}

/// The open preview closed; later closes of the same preview record nothing.
pub fn closed(app: &mut App, close: Close) {
    finish(app, End::Closed(close));
}

/// Marks the preview as copied from when the copied selection is its own; returns the copy's source.
pub fn note_copy(app: &mut App) -> Option<&'static str> {
    let owner = app.selection.region.as_ref()?.owner;
    if owner != RegionId::CodePreview {
        return None;
    }
    if let Some(visit) = app.code_preview.visit.as_mut() {
        visit.has_copied = true;
    }
    Some(COPY_SOURCE)
}

fn finish(app: &mut App, end: End) {
    let Some(visit) = app.code_preview.visit.take() else {
        return;
    };
    let status = match (&end, visit.rendered_at) {
        (End::Failed(_), _) => "error",
        (End::Closed(_), Some(_)) => "success",
        (End::Closed(_), None) => "cancelled",
    };
    let mut properties = visit.link;
    properties.extend(bmap(json!({
        "status": status,
        "has_scrolled": visit.has_scrolled,
        "has_copied": visit.has_copied,
        "is_scrollable": app.code_preview.max_scroll > 0,
    })));
    let extra = match end {
        End::Failed(failure) => json!({"error_type": failure.name()}),
        End::Closed(close) => json!({"close_reason": close.name()}),
    };
    properties.extend(bmap(extra));
    if let Some(at) = visit.rendered_at {
        let duration_ms = crate::clock::since(at).as_millis() as u64;
        properties.insert("duration_ms".to_owned(), duration_ms.into());
    }
    record(app, event::CODE_PREVIEW_DONE, properties);
}

fn link_properties(location: &Location) -> BTreeMap<String, Value> {
    let (kind, scheme) = match location.kind {
        Kind::Path => ("path", None),
        Kind::FileUrl => ("file_url", None),
        Kind::Editor(scheme) => ("editor", Some(scheme)),
    };
    let mut properties = bmap(json!({
        "link_kind": kind,
        "has_linked_line": location.lines.is_some(),
    }));
    if let Some(scheme) = scheme {
        properties.insert("editor_scheme".to_owned(), scheme.into());
    }
    properties
}
