//! Ending a captured gesture before its release.

use super::{scrollbar, Capture, MouseTarget};
use crate::app::App;
use crate::selection;

/// End the captured gesture, keeping (and copying) the selection in flight or aborting it.
pub(crate) fn cancel_capture(app: &mut App, keep_selection: bool) {
    if let Some(Capture::Target(target)) = app.view.mouse.capture.take() {
        if matches!(
            target,
            MouseTarget::Transcript
                | MouseTarget::Composer
                | MouseTarget::Trust
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
                | MouseTarget::ArtifactsModal
                | MouseTarget::ResumePicker
                | MouseTarget::Mcp
                | MouseTarget::Plugins
                | MouseTarget::Skills
                | MouseTarget::SkillsBody
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
