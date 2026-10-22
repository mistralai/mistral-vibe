//! Ending a captured gesture before its release.

use super::{scrollbar, Capture, MouseTarget};
use crate::app::App;
use crate::selection;

/// End the captured gesture, keeping (and copying) the selection in flight or aborting it.
pub(crate) fn cancel_capture(app: &mut App, keep_selection: bool) {
    if let Some(Capture::Target(target)) = app.view.mouse.capture.take() {
        if target == MouseTarget::RemoteProject {
            app.vibe_code_project.list.cancel();
            app.vibe_code_project.dragged_field = None;
        }
        if matches!(
            target,
            MouseTarget::Transcript
                | MouseTarget::Toast
                | MouseTarget::Composer
                | MouseTarget::Trust
                | MouseTarget::RemoteProject
                | MouseTarget::BottomBar
                | MouseTarget::Loading
                | MouseTarget::Question
                | MouseTarget::Approval
                | MouseTarget::BottomApp
                | MouseTarget::ProxySetup
                | MouseTarget::ThemePicker
                | MouseTarget::ModelPicker
                | MouseTarget::ThinkingPicker
                | MouseTarget::LogLevelPicker
                | MouseTarget::ResumePicker
                | MouseTarget::Mcp
                | MouseTarget::Plugins
                | MouseTarget::McpOAuth
                | MouseTarget::ConnectorAuth
        ) {
            if keep_selection {
                selection::end_drag(app);
            } else {
                selection::cancel_drag(app);
            }
        }
    }
    scrollbar::cancel(app);
}
