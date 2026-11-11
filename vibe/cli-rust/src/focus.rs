//! The surface that owns the keyboard and the screen, in one precedence order.

use crate::app::App;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Focus {
    Trust,
    Config,
    ReleaseNotes,
    Approval,
    Question,
    VoiceApp,
    BranchApp,
    ProxySetup,
    ResumePicker,
    Mcp,
    Plugins,
    Skills,
    ContextApp,
    McpOAuth,
    ConnectorAuth,
    Rewind,
    ThemePicker,
    ModelPicker,
    LogLevelPicker,
    ThinkingPicker,
    ArtifactsModal,
    SubagentList,
    Composer,
}

impl App {
    /// The first open surface, checked from highest to lowest precedence.
    pub fn focus(&self) -> Focus {
        [
            (self.trust.open, Focus::Trust),
            (self.config_screen.open, Focus::Config),
            (self.release_notes.open, Focus::ReleaseNotes),
            (self.approval.open, Focus::Approval),
            (self.question_app.open, Focus::Question),
            (self.voice_app.open, Focus::VoiceApp),
            (self.branch_app.open, Focus::BranchApp),
            (self.proxy_setup.open, Focus::ProxySetup),
            (self.resume_picker.open, Focus::ResumePicker),
            (self.mcp.open, Focus::Mcp),
            (self.plugins.open, Focus::Plugins),
            (self.skills_browser.open, Focus::Skills),
            (self.context_app.open, Focus::ContextApp),
            (self.mcp_oauth.open, Focus::McpOAuth),
            (self.connector_auth.open, Focus::ConnectorAuth),
            (self.rewind.open, Focus::Rewind),
            (self.theme_picker.open, Focus::ThemePicker),
            (self.model_picker.open, Focus::ModelPicker),
            (self.log_level_picker.open, Focus::LogLevelPicker),
            (self.thinking_picker.open, Focus::ThinkingPicker),
            (self.artifacts_modal.open, Focus::ArtifactsModal),
            (self.subagents.list.focused, Focus::SubagentList),
        ]
        .into_iter()
        .find_map(|(open, focus)| open.then_some(focus))
        .unwrap_or(Focus::Composer)
    }

    /// Whether a picker the user opened, or is opening, owns Esc and Ctrl+C over the turn.
    pub fn picker_focused(&self) -> bool {
        !matches!(
            self.focus(),
            Focus::Approval | Focus::Question | Focus::Composer | Focus::SubagentList
        )
    }
}
