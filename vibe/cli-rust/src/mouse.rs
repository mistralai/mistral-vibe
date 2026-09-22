//! Paint-ordered mouse routing with gesture capture.

mod dispatch;
mod scrollbar;

pub use scrollbar::register_scrollbar;

use std::sync::Arc;

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use tokio::sync::mpsc;

use crate::app::App;
use crate::selection;
use crate::server::Client;
use crate::{
    approval, completion_manager, config, log_level_picker, mcp, model_picker, resume_picker,
    theme_picker,
};
use dispatch::dispatch;

/// Lines a shift+arrow keypress scrolls the transcript (Python `scroll_relative(y=±5)`).
pub const KEY_SCROLL_STEP: u16 = 5;
/// Lines one wheel notch scrolls (Textual's `scroll_sensitivity_y`).
pub(crate) const MOUSE_SCROLL_STEP: u16 = 2;

/// A behavior attached to a painted rectangle during the latest render.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MouseTarget {
    Blocked,
    Transcript,
    Toast,
    Composer,
    Completion,
    Approval,
    Question,
    ThemePicker,
    ModelPicker,
    LogLevelPicker,
    ResumePicker,
    Rewind,
    Mcp,
    McpOAuth,
    ConnectorAuth,
    Config,
    ConfigEditor,
    Trust,
    BottomBar,
}

/// Render-time hit-test metadata for one screen region.
pub struct MouseRegion {
    pub(super) area: Rect,
    pub(super) target: MouseTarget,
    pub(super) scrollbar: Option<crate::ui::scrollbar::State>,
}

#[derive(Clone, Copy)]
enum Capture {
    Scrollbar,
    Target(MouseTarget),
}

/// Mouse gesture state owned by the central router.
#[derive(Default)]
pub struct MouseState {
    capture: Option<Capture>,
    scrollbars: scrollbar::Scrollbars,
}

impl MouseState {
    pub fn is_dragging_scrollbar(&self) -> bool {
        matches!(self.capture, Some(Capture::Scrollbar))
    }
}

/// Register a mouse target in paint order; later overlaps win.
/// The list is rebuilt every frame, so its size follows the painted UI.
pub fn register_region(app: &mut App, area: Rect, target: MouseTarget) {
    if area.is_empty() {
        return;
    }
    app.view.mouse_regions.push(MouseRegion {
        area,
        target,
        scrollbar: None,
    });
}

/// Return the topmost painted target under a screen cell.
pub fn target_at(app: &App, at: (u16, u16)) -> Option<MouseTarget> {
    app.view
        .mouse_regions
        .iter()
        .rev()
        .find(|region| contains(region.area, at))
        .map(|region| region.target)
}

/// Route every terminal mouse event through the latest painted region map.
pub(crate) fn handle(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    event: MouseEvent,
) {
    if let Some(target) = route(app, event) {
        dispatch(app, client, config_tx, target, event);
    }
}

/// Resolve ownership and apply gestures handled entirely by the router.
pub fn route(app: &mut App, event: MouseEvent) -> Option<MouseTarget> {
    let at = (event.column, event.row);
    if event.kind == MouseEventKind::Moved {
        app.view.mouse_position = Some(at);
    }
    if handle_wheel(app, event) {
        return None;
    }
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            cancel_capture(app);
            if scrollbar::begin(app, at) {
                app.view.mouse.capture = Some(Capture::Scrollbar);
                return None;
            }
            let target = target_at(app, at)?;
            app.view.mouse.capture = Some(Capture::Target(target));
            Some(target)
        }
        MouseEventKind::Drag(MouseButton::Left) => match app.view.mouse.capture {
            Some(Capture::Scrollbar) => {
                scrollbar::drag(app, event.row);
                None
            }
            Some(Capture::Target(target)) => Some(target),
            None => None,
        },
        MouseEventKind::Up(MouseButton::Left) => match app.view.mouse.capture.take() {
            Some(Capture::Scrollbar) => {
                scrollbar::end(app);
                None
            }
            Some(Capture::Target(target)) => Some(target),
            None => None,
        },
        _ => target_at(app, at),
    }
}

/// Route a wheel event to the topmost region under the pointer.
fn handle_wheel(app: &mut App, event: MouseEvent) -> bool {
    let up = match event.kind {
        MouseEventKind::ScrollUp => true,
        MouseEventKind::ScrollDown => false,
        _ => return false,
    };
    cancel_scrollbar_capture(app);
    let Some(target) = target_at(app, (event.column, event.row)) else {
        return true;
    };
    match target {
        MouseTarget::Transcript => scroll_chat(app, up, MOUSE_SCROLL_STEP),
        MouseTarget::Composer => scroll_input(app, up),
        MouseTarget::Completion => completion_manager::wheel(app, !up, MOUSE_SCROLL_STEP as usize),
        MouseTarget::ThemePicker => theme_picker::navigate(app, !up),
        MouseTarget::ModelPicker => model_picker::navigate(app, !up),
        MouseTarget::LogLevelPicker => log_level_picker::navigate(app, !up),
        MouseTarget::ResumePicker => resume_picker::wheel(app, !up),
        MouseTarget::Mcp => mcp::wheel(app, !up),
        MouseTarget::Config => config::wheel(app, if up { -2 } else { 2 }),
        MouseTarget::ConfigEditor => crate::config_edit::wheel(app, if up { -1 } else { 1 }),
        MouseTarget::Trust => crate::trust_folders::wheel(app, up),
        MouseTarget::Approval => approval::handle_mouse(app, event),
        MouseTarget::Question => crate::question_input::wheel(app, up),
        MouseTarget::Blocked
        | MouseTarget::Toast
        | MouseTarget::Rewind
        | MouseTarget::McpOAuth
        | MouseTarget::ConnectorAuth
        | MouseTarget::BottomBar => {}
    }
    true
}

pub(crate) fn cancel_capture(app: &mut App) {
    if matches!(
        app.view.mouse.capture.take(),
        Some(Capture::Target(
            MouseTarget::Transcript
                | MouseTarget::Toast
                | MouseTarget::Composer
                | MouseTarget::Trust
                | MouseTarget::BottomBar
        ))
    ) {
        selection::cancel_drag(app);
    }
    scrollbar::cancel(app);
}

fn cancel_scrollbar_capture(app: &mut App) {
    if matches!(app.view.mouse.capture, Some(Capture::Scrollbar)) {
        app.view.mouse.capture = None;
        scrollbar::cancel(app);
    }
}

pub fn scroll_chat(app: &mut App, up: bool, step: u16) {
    selection::scrolled(app);
    if up {
        app.view.scroll_target = app.view.scroll_target.saturating_add(step);
    } else {
        app.view.scroll_target = app.view.scroll_target.saturating_sub(step);
    }
}

fn contains(area: Rect, at: (u16, u16)) -> bool {
    at.0 >= area.x && at.0 < area.right() && at.1 >= area.y && at.1 < area.bottom()
}

fn scroll_input(app: &mut App, up: bool) {
    let area = app.view.input_area;
    let viewport = area.height.saturating_sub(2) as usize;
    let scroll = crate::ui::chat_input::scroll(app, area.width, viewport);
    app.chat_input.scroll = Some(if up {
        scroll.saturating_sub(MOUSE_SCROLL_STEP)
    } else {
        scroll.saturating_add(MOUSE_SCROLL_STEP)
    });
}
