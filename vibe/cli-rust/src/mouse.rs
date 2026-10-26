//! Paint-ordered mouse routing with gesture capture.

mod capture;
mod dispatch;
mod option_gesture;
pub mod pixels;
mod scrollbar;

pub use option_gesture::option_gesture;
pub use scrollbar::{drag_scroll, register_scrollbar, scrollbar_tracks};

pub(crate) use capture::cancel_capture;
pub use dispatch::toggle_effect_at;

use std::sync::Arc;

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use tokio::sync::mpsc;

use crate::app::App;
use crate::selection;
use crate::server::Client;
use crate::{
    approval, completion_manager, config, log_level_picker, model_picker, resume_picker,
    theme_picker, thinking_picker,
};
use dispatch::dispatch;
pub(crate) use dispatch::option_list;

/// Lines a shift+arrow keypress scrolls the transcript.
pub const KEY_SCROLL_STEP: u16 = 5;
/// Lines one wheel notch scrolls.
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
    Loading,
    ThemePicker,
    ModelPicker,
    ThinkingPicker,
    LogLevelPicker,
    ArtifactsModal,
    ResumePicker,
    RemoteProject,
    ProxySetup,
    BottomApp,
    Mcp,
    Plugins,
    McpOAuth,
    ConnectorAuth,
    DebugConsole,
    Config,
    ConfigEditor,
    Trust,
    BottomBar,
    TodoRow,
    TodoSidebar,
    OnboardingThemeList,
    OnboardingPreview,
    OnboardingLinks,
    OnboardingInputs,
    SubagentList,
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
    ScrollbarTrack,
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

    /// A press on a target owns the pointer until its release: the gesture that holds cell reports.
    pub fn holds_target(&self) -> bool {
        matches!(self.capture, Some(Capture::Target(_)))
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

pub(crate) fn scrollbar_at(app: &App, at: (u16, u16)) -> bool {
    scrollbar::contains_track(app, at)
}

pub fn is_track_paging(app: &App) -> bool {
    scrollbar::is_track_paging(app)
}

/// Advance a held scrollbar track click by one auto-scroll tick.
pub fn repeat_track_page(app: &mut App) {
    scrollbar::repeat_track(app);
}

/// Route every terminal mouse event through the latest painted region map.
pub fn handle(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    event: MouseEvent,
) {
    let (event, row) = pixels::to_cells(event);
    if let Some(target) = route_at(app, event, row) {
        dispatch(app, client, config_tx, target, event);
    }
}

/// Route a cell-precise event.
pub fn route(app: &mut App, event: MouseEvent) -> Option<MouseTarget> {
    route_at(app, event, f64::from(event.row))
}

/// Resolve ownership and apply router-only gestures; `row` is the pointer row in fractional cells.
pub fn route_at(app: &mut App, event: MouseEvent, row: f64) -> Option<MouseTarget> {
    let at = (event.column, event.row);
    if event.kind == MouseEventKind::Moved {
        app.view.mouse_position = Some(at);
        // No button is held, so the release was lost (pixel reports drop one past the top or left edge).
        if app.view.mouse.capture.is_some() {
            cancel_capture(app, true);
        }
    }
    if handle_wheel(app, event) {
        return None;
    }
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            cancel_capture(app, true);
            selection::honor_pending_copy(app);
            if scrollbar::contains_track(app, at) {
                if scrollbar::begin(app, at, row) {
                    app.view.mouse.capture = Some(Capture::Scrollbar);
                } else if scrollbar::begin_track(app, at) {
                    app.view.mouse.capture = Some(Capture::ScrollbarTrack);
                }
                return None;
            }
            let target = target_at(app, at)?;
            app.view.mouse.capture = Some(Capture::Target(target));
            Some(target)
        }
        MouseEventKind::Drag(MouseButton::Left) => match app.view.mouse.capture {
            Some(Capture::Scrollbar) => {
                scrollbar::drag(app, row);
                None
            }
            Some(Capture::ScrollbarTrack) => {
                scrollbar::move_track(app, at);
                None
            }
            Some(Capture::Target(target)) => Some(target),
            None => None,
        },
        MouseEventKind::Up(MouseButton::Left) => match app.view.mouse.capture.take() {
            Some(Capture::Scrollbar | Capture::ScrollbarTrack) => {
                scrollbar::cancel(app);
                None
            }
            Some(Capture::Target(target)) => Some(target),
            None => None,
        },
        // The hover marker follows the mouse
        // and clears once it leaves the list.
        _ => {
            let target = target_at(app, at);
            if event.kind == MouseEventKind::Moved && target != Some(MouseTarget::SubagentList) {
                app.subagents.list.mouse_session_id = None;
            }
            target
        }
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
    // Scrolling a bottom-app box moves its text under a screen-anchored selection.
    if app
        .view
        .bottom_app_selection_region
        .contains((event.column, event.row))
    {
        selection::clear_region(app, selection::RegionId::BottomApp);
    }
    match target {
        // The console's wheel scrolls its log and may load older pages, which
        // needs the client: dispatch (not this client-free router) runs it.
        MouseTarget::DebugConsole => return false,
        MouseTarget::Transcript => scroll_chat(app, up, MOUSE_SCROLL_STEP),
        MouseTarget::Composer => scroll_input(app, up),
        MouseTarget::Completion => completion_manager::wheel(app, !up, MOUSE_SCROLL_STEP as usize),
        MouseTarget::ThemePicker => theme_picker::navigate(app, !up),
        MouseTarget::ModelPicker => model_picker::navigate(app, !up),
        MouseTarget::ThinkingPicker => thinking_picker::navigate(app, !up),
        MouseTarget::LogLevelPicker => log_level_picker::navigate(app, !up),
        MouseTarget::ArtifactsModal => crate::artifacts_modal::navigate(app, !up),
        MouseTarget::ResumePicker => resume_picker::wheel(app, !up),
        MouseTarget::Mcp
        | MouseTarget::RemoteProject
        | MouseTarget::Config
        | MouseTarget::SubagentList
        | MouseTarget::Question => {
            if let Some(list) = option_list(app, target) {
                list.wheel(!up, MOUSE_SCROLL_STEP as usize);
            }
        }
        MouseTarget::Plugins => crate::plugins::wheel(app, !up),
        MouseTarget::ProxySetup => app.proxy_setup.wheel(up, MOUSE_SCROLL_STEP as usize),
        MouseTarget::ConfigEditor => crate::config_edit::wheel(app, if up { -1 } else { 1 }),
        MouseTarget::Trust => crate::trust_folders::wheel(app, up),
        MouseTarget::TodoSidebar => {
            let scroll = &mut app.todo_sidebar.scroll;
            *scroll = if up {
                scroll.saturating_sub(MOUSE_SCROLL_STEP as usize)
            } else {
                scroll.saturating_add(MOUSE_SCROLL_STEP as usize)
            };
        }
        MouseTarget::Approval => approval::wheel(app, !up),
        // The wizard's own regions never reach the loop: the wizard is a
        // pre-session surface with its own mouse handling.
        MouseTarget::Blocked
        | MouseTarget::Loading
        | MouseTarget::Toast
        | MouseTarget::BottomApp
        | MouseTarget::McpOAuth
        | MouseTarget::ConnectorAuth
        | MouseTarget::BottomBar
        | MouseTarget::OnboardingThemeList
        | MouseTarget::OnboardingPreview
        | MouseTarget::OnboardingLinks
        | MouseTarget::OnboardingInputs
        | MouseTarget::TodoRow => {}
    }
    true
}

fn cancel_scrollbar_capture(app: &mut App) {
    if matches!(
        app.view.mouse.capture,
        Some(Capture::Scrollbar | Capture::ScrollbarTrack)
    ) {
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
