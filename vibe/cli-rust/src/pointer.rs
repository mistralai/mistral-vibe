//! Mouse pointer shape (Kitty OSC 22), mirroring Textual's `pointer` CSS.

use std::io::Write;

use crate::app::App;
use crate::selection;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Shape {
    #[default]
    Default,
    /// Hand: something a click acts on (Python `pointer: pointer`).
    Pointer,
    /// I-beam: editable or selectable text (Python `pointer: text`).
    Text,
    /// Closed hand while the transcript scrollbar thumb is being dragged.
    Grabbing,
}

impl Shape {
    fn name(self) -> &'static str {
        match self {
            Shape::Default => "default",
            Shape::Pointer => "pointer",
            Shape::Text => "text",
            Shape::Grabbing => "grabbing",
        }
    }
}

/// Emit the shape the cell under the mouse asks for, when it changed.
pub fn sync(app: &mut App) {
    let shape = shape(app);
    if shape == app.view.pointer_shape {
        return;
    }
    app.view.pointer_shape = shape;
    emit(shape);
}

pub fn emit(shape: Shape) {
    let mut out = std::io::stdout();
    let _ = write!(out, "\x1b]22;{}\x07", shape.name());
    let _ = out.flush();
}

/// The shape for the current hover, following what a click there would do.
pub fn shape(app: &App) -> Shape {
    if app.view.mouse.is_dragging_scrollbar() {
        return Shape::Grabbing;
    }
    // A drag in flight owns the pointer (Python `Screen._selecting`).
    if app.selection.drag.is_some() {
        return Shape::Text;
    }
    let Some(at) = app.view.mouse_position else {
        return Shape::Default;
    };
    let Some(target) = crate::mouse::target_at(app, at) else {
        return Shape::Default;
    };
    if target == crate::mouse::MouseTarget::Composer {
        return if selection::in_input(app, at) {
            Shape::Text
        } else {
            Shape::Default
        };
    }
    if target == crate::mouse::MouseTarget::Toast {
        return Shape::Text;
    }
    if target == crate::mouse::MouseTarget::Trust {
        return if app.view.selection_region.contains(at) {
            Shape::Text
        } else {
            Shape::Default
        };
    }
    if target != crate::mouse::MouseTarget::Transcript {
        return Shape::Default;
    }
    if app.view.link_hitmap.iter().any(|link| link.contains(at)) {
        return Shape::Pointer;
    }
    if expandable_at(app, at) {
        return Shape::Pointer;
    }
    Shape::Default
}

/// Whether `at` sits on a transcript entry a click would fold or unfold.
fn expandable_at(app: &App, at: (u16, u16)) -> bool {
    if !app.view.selection_region.contains(at) {
        return false;
    }
    app.view
        .entry_hitmap
        .iter()
        .find(|(top, bottom, _)| at.1 >= *top && at.1 < *bottom)
        .is_some_and(|(_, _, id)| app.view.transcript.is_expandable(id))
}
