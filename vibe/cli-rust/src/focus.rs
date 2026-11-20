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
    Storage,
    Mcp,
    Plugins,
    TrustedFolders,
    Skills,
    ContextApp,
    McpOAuth,
    ConnectorAuth,
    Rewind,
    ThemePicker,
    ModelPicker,
    LogLevelPicker,
    ThinkingPicker,
    Loops,
    Goal,
    RailWindow,
    SubagentList,
    Rail,
    Composer,
}

impl Focus {
    /// The rail, an open rail window or the subagent list: the surfaces Ctrl+X and window switching reach.
    pub fn is_rail_surface(self) -> bool {
        matches!(self, Focus::Rail | Focus::RailWindow | Focus::SubagentList)
    }
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
            (self.storage.open, Focus::Storage),
            (self.mcp.open, Focus::Mcp),
            (self.plugins.open, Focus::Plugins),
            (self.trusted_folders.open, Focus::TrustedFolders),
            (self.skills_browser.open, Focus::Skills),
            (self.context_app.open, Focus::ContextApp),
            (self.mcp_oauth.open, Focus::McpOAuth),
            (self.connector_auth.open, Focus::ConnectorAuth),
            (self.rewind.open, Focus::Rewind),
            (self.theme_picker.open, Focus::ThemePicker),
            (self.model_picker.open, Focus::ModelPicker),
            (self.log_level_picker.open, Focus::LogLevelPicker),
            (self.thinking_picker.open, Focus::ThinkingPicker),
            (self.rail.focused && self.rail.letters, Focus::Rail),
            (self.loops.open, Focus::Loops),
            (self.goal.panel.is_some(), Focus::Goal),
            (crate::rail::window(self).is_some(), Focus::RailWindow),
            (self.subagents.list.focused, Focus::SubagentList),
            (self.rail.focused, Focus::Rail),
        ]
        .into_iter()
        .find_map(|(open, focus)| open.then_some(focus))
        .unwrap_or(Focus::Composer)
    }

    /// Whether a picker the user opened, or is opening, owns Esc and Ctrl+C over the turn.
    pub fn picker_focused(&self) -> bool {
        !matches!(
            self.focus(),
            Focus::Approval | Focus::Question | Focus::Composer | Focus::SubagentList | Focus::Rail
        )
    }
}

/// Whether the focused surface moves through something with Tab, so Shift+Tab moves back through it instead of cycling agents.
pub fn tab_navigates(app: &App) -> bool {
    match app.focus() {
        Focus::Loops => app.loops.form.is_some(),
        Focus::Goal | Focus::ProxySetup => true,
        // A child view is no rail surface: Shift+Tab cycles agents there.
        Focus::SubagentList if app.subagents.viewed_subagent_id.is_some() => false,
        // The rail and its windows walk their items with Tab and Shift+Tab.
        focus if focus.is_rail_surface() => true,
        Focus::Skills => {
            let browser = &app.skills_browser;
            browser.confirm.is_some() || (browser.registry && browser.page.is_none())
        }
        Focus::Config => app.config_screen.edit.is_some() && !app.config_screen.targets.is_empty(),
        _ => false,
    }
}
