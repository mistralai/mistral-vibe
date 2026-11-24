//! Target-specific mouse behavior after routing and capture.

use std::sync::Arc;

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use tokio::sync::mpsc;

use super::{option_gesture, MouseTarget};
use crate::app::App;
use crate::config;
use crate::option_list::{Click, MultiAction, OptionList, OptionView, SingleAction};
use crate::selection::{self, RegionId, Release};
use crate::server::Client;
use crate::utils::scroll::ScrollAnchor;
use crate::{
    approval, artifacts_modal, connector_auth, external_url, mcp, mcp_oauth, model_picker,
    question_input, thinking_picker,
};

pub(super) fn dispatch(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    target: MouseTarget,
    event: MouseEvent,
) {
    match target {
        MouseTarget::Transcript | MouseTarget::Composer => {
            selection_event(app, client, target, event)
        }
        MouseTarget::BottomBar => bottom_bar_event(app, client, event),
        MouseTarget::Loading => loading_event(app, event),
        MouseTarget::CodePreview => owned_region_event(app, event, |app, at| {
            selection::press_owned(app, at, RegionId::CodePreview)
        }),
        MouseTarget::GoalRow => owned_region_event(app, event, |app, at| {
            selection::press_owned(app, at, selection::RegionId::Goal)
        }),
        MouseTarget::Question => question_input::handle_mouse(app, client, event),
        MouseTarget::Approval => approval::handle_mouse(app, client, event),
        MouseTarget::Mcp => mcp::mouse(app, event),
        MouseTarget::Plugins => crate::plugins::mouse(app, event),
        MouseTarget::TrustedFolders => crate::trusted_folders::mouse(app, client, event),
        MouseTarget::Skills => crate::skills_browser::mouse(app, client, event),
        MouseTarget::SkillsBody | MouseTarget::LoopsDetails => {
            bottom_app_event(app, event, |_, _| {})
        }
        MouseTarget::ReleaseNotes => crate::release_notes::mouse(app, event),
        MouseTarget::McpOAuth => {
            if picked(app, event, |app| &mut app.mcp_oauth.list) {
                mcp_oauth::select(app);
            }
        }
        MouseTarget::ConnectorAuth => {
            if picked(app, event, |app| &mut app.connector_auth.list) {
                connector_auth::select(app);
            }
        }
        MouseTarget::ModelPicker => model_picker::mouse(app, event, client),
        MouseTarget::ThemePicker => {
            if picked(app, event, |app| &mut app.theme_picker.list) {
                crate::input::select_theme(app, client);
            }
        }
        // A click highlights a task and a click on the highlight shows its output; stopping one takes `s`.
        MouseTarget::TasksPanel => {
            if app.tasks_panel.list.confirmed(event).is_some() {
                crate::tasks_panel::show_output(app, client);
            }
        }
        MouseTarget::ThinkingPicker => {
            if confirmed(app, event, |app| &mut app.thinking_picker.list) {
                let scope = thinking_picker::enter_scope(app);
                thinking_picker::select(app, client, scope);
            }
        }
        MouseTarget::SessionsPage | MouseTarget::SessionsPreview => {
            crate::sessions_page::mouse::dispatch(app, client, event)
        }
        MouseTarget::ProxySetup => bottom_app_event(app, event, |app, event| {
            if event.kind == MouseEventKind::Down(MouseButton::Left) {
                crate::proxy_setup::press(app, (event.column, event.row));
            }
        }),
        MouseTarget::LoopsForm => bottom_app_event(app, event, |app, event| {
            if event.kind == MouseEventKind::Down(MouseButton::Left) {
                if let Some(form) = app.loops.form.as_mut() {
                    form.press((event.column, event.row));
                }
            }
        }),
        MouseTarget::LogLevelPicker => {
            if picked(app, event, |app| &mut app.log_level_picker.list) {
                crate::log_level_picker::click(app, (event.column, event.row));
            }
        }
        MouseTarget::ArtifactsModal => {
            if confirmed(app, event, |app| &mut app.artifacts_modal.list) {
                artifacts_modal::select(app);
            }
        }
        // A click highlights a loop and a click on the highlight edits it; deleting stays on `d`.
        MouseTarget::Loops => {
            if confirmed(app, event, |app| &mut app.loops.list) {
                crate::loops::edit(app);
            }
        }
        MouseTarget::GoalConfirm => {
            if picked(app, event, |app| &mut app.goal.list) {
                crate::goal::choose(app, client);
            }
        }
        MouseTarget::BottomApp => bottom_app_event(app, event, |_, _| {}),
        MouseTarget::Trust => crate::trust_folders::handle_mouse(app, event),
        MouseTarget::JumpToBottom => {
            if matches!(event.kind, MouseEventKind::Down(MouseButton::Left)) {
                crate::mouse::scroll_chat(app, false, u16::MAX);
            }
        }
        MouseTarget::StickyPrompt => {
            if matches!(event.kind, MouseEventKind::Down(MouseButton::Left)) {
                let steer = app.view.sticky_steer.filter(|&(_, row)| row == event.row);
                let prompt = app.view.sticky_prompt.map(|(index, _)| index);
                app.view.scroll_to_entry = steer.map(|(index, _)| index).or(prompt);
            }
        }
        // A press on a task output's search row focuses it; one on its text leaves it and selects.
        MouseTarget::RailWindow => owned_region_event(app, event, |app, at| {
            let search = app.tasks_panel.shown_mut().map(|view| &mut view.search);
            let focused = search.is_some_and(|search| {
                search.focused = search.area.contains(at.into());
                search.focused
            });
            if !focused {
                selection::press_owned(app, at, selection::RegionId::RailWindow);
            }
        }),
        MouseTarget::Rail => {
            let press = matches!(event.kind, MouseEventKind::Down(MouseButton::Left))
                && crate::rail::item_at(app, event.column, event.row).is_some();
            if press && crate::rail::leave_box(app, client) {
                crate::rail::click(app, client, event.column, event.row);
            }
        }
        MouseTarget::TodoSidebar => {}
        // The console log body: the wheel scrolls it (loading older pages at
        // the top) and a click copies the log line. The
        // router keeps the pressed target wherever the pointer is released,
        // and `entry_at` matches on the row alone, so the body gates.
        MouseTarget::DebugConsole => {
            if !app
                .debug_console
                .body_area
                .contains((event.column, event.row).into())
            {
                return;
            }
            match event.kind {
                MouseEventKind::ScrollUp => crate::debug_console::wheel(app, client, true),
                MouseEventKind::ScrollDown => crate::debug_console::wheel(app, client, false),
                MouseEventKind::Up(MouseButton::Left) => {
                    crate::debug_console::copy_entry_at(app, event.row)
                }
                _ => {}
            }
        }
        MouseTarget::SubagentList => subagent_list_event(app, event),
        MouseTarget::Config | MouseTarget::ConfigEditor => {
            config::handle_mouse(app, client, config_tx, event)
        }
        // The wizard's regions never reach the loop (the wizard runs
        // pre-session and routes its own mouse events).
        MouseTarget::Blocked
        | MouseTarget::Completion
        | MouseTarget::OnboardingThemeList
        | MouseTarget::OnboardingPreview
        | MouseTarget::OnboardingLinks
        | MouseTarget::OnboardingInputs => {}
    }
}

/// The scrollable option list a mouse target shows, if any.
pub(crate) fn option_list(app: &mut App, target: MouseTarget) -> Option<&mut dyn OptionView> {
    Some(match target {
        MouseTarget::ThemePicker => &mut app.theme_picker.list,
        MouseTarget::ModelPicker => &mut app.model_picker.list,
        MouseTarget::ThinkingPicker => &mut app.thinking_picker.list,
        MouseTarget::TasksPanel => &mut app.tasks_panel.list,
        MouseTarget::LogLevelPicker => &mut app.log_level_picker.list,
        MouseTarget::ArtifactsModal => &mut app.artifacts_modal.list,
        MouseTarget::Loops => &mut app.loops.list,
        MouseTarget::GoalConfirm => &mut app.goal.list,
        MouseTarget::SessionsPage => crate::sessions_page::mouse::list(app),
        MouseTarget::Mcp => &mut app.mcp.list,
        MouseTarget::Plugins => &mut app.plugins.list,
        MouseTarget::TrustedFolders => {
            crate::trusted_folders::visible_list(&mut app.trusted_folders)
        }
        MouseTarget::Skills => crate::skills_browser::visible_list(&mut app.skills_browser),
        MouseTarget::Config => &mut app.config_screen.list,
        MouseTarget::ConfigEditor => &mut app.config_screen.edit.as_mut()?.list,
        MouseTarget::Question => &mut app.question_app.list,
        MouseTarget::SubagentList => &mut app.subagents.list.options,
        _ => return None,
    })
}

/// A completed click on an option of a single-action bottom-app list.
fn picked(
    app: &mut App,
    event: MouseEvent,
    list: fn(&mut App) -> &mut OptionList<SingleAction>,
) -> bool {
    option_gesture(app, event, RegionId::BottomApp, list).is_some()
}

/// A click on the highlighted option of a multi-action bottom-app list.
fn confirmed(
    app: &mut App,
    event: MouseEvent,
    list: fn(&mut App) -> &mut OptionList<MultiAction>,
) -> bool {
    let click = option_gesture(app, event, RegionId::BottomApp, list);
    matches!(click, Some(Click::Confirm(_)))
}

/// Drags select text; a press, and a release on the pressed cell, also click.
fn bottom_app_event(app: &mut App, event: MouseEvent, click: impl FnOnce(&mut App, MouseEvent)) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            click(app, event);
            selection::press_owned(app, at, RegionId::BottomApp);
        }
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            let same_cell = app.selection.press == Some(at);
            let selected = matches!(selection::release(app), Release::Selected);
            if same_cell && !selected {
                click(app, event);
            }
        }
        _ => {}
    }
}

/// The subagent list: hover moves the marker and a click selects the row.
fn subagent_list_event(app: &mut App, event: MouseEvent) {
    let index = app
        .subagents
        .list
        .options
        .index_at((event.column, event.row));
    match event.kind {
        MouseEventKind::Moved => {
            let hover = index.map(|index| {
                crate::subagents::list::session_id_at(app, index)
                    .unwrap_or_else(|| crate::subagents::MAIN_SESSION_ID.to_owned())
            });
            crate::subagents::list::set_hover(app, hover);
        }
        MouseEventKind::Down(MouseButton::Left) if index.is_some() => {
            crate::reverse_search::cancel(app);
            app.subagents.list.focused = true;
            app.set_app_focus(false);
            crate::selection::blur_composer(app);
        }
        _ => {}
    }
    if let Some(index) = app.subagents.list.options.mouse(event) {
        let session_id = crate::subagents::list::session_id_at(app, index);
        crate::subagents::select(app, session_id);
    }
}

/// Selecting an owned region copies its text; it has no click action.
fn owned_region_event(app: &mut App, event: MouseEvent, press: fn(&mut App, (u16, u16))) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => press(app, at),
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            selection::release(app);
        }
        _ => {}
    }
}

/// Selecting the loading row copies its text; the row owns no click action.
fn loading_event(app: &mut App, event: MouseEvent) {
    owned_region_event(app, event, |app, at| {
        selection::press_owned(app, at, selection::RegionId::Loading)
    });
}

fn bottom_bar_event(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            // Honor a copy armed by the previous release before this press drops
            // it; the text was cached at paint time, so no buffer is read.
            crate::ui::bottom_bar::honor_pending_copy(app);
            app.overlays.notice = None;
            // A press on a clickable item is a button press: no text selection, no click chain.
            if crate::ui::bottom_bar::clickable_at(app, at) {
                selection::cancel_drag(app);
                app.selection.chain = Default::default();
                app.selection.press = Some(at);
            } else {
                selection::bottom_bar::press(app, at);
            }
        }
        MouseEventKind::Drag(MouseButton::Left) => selection::bottom_bar::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            // The press cell and drag flag are cleared by release; read them first.
            let pressed = app.selection.press;
            let dragged = app.selection.dragged;
            let release = selection::release(app);
            // A press and release on the copy icon copies the cwd.
            if !matches!(release, Release::Selected) {
                let action = crate::ui::bottom_bar::link_at(app, at)
                    .filter(|link| pressed.is_some_and(|press| link.area.contains(press.into())))
                    .map(|link| link.action.clone());
                match action {
                    Some(crate::ui::bottom_bar::BarLinkAction::CopyPath(path)) => {
                        crate::ui::notice::copied(app, &path)
                    }
                    None => {}
                }
            }
            // A plain click on the context gauge toggles `/context`, a side-channel
            // read that also works while a turn generates.
            let tokens_click = !dragged
                && pressed.is_some_and(|press| crate::ui::bottom_bar::tokens_at(app, press))
                && crate::ui::bottom_bar::tokens_at(app, at);
            if matches!(release, Release::Nothing) && tokens_click {
                if app.context_app.open {
                    crate::context_app::close(app);
                } else {
                    crate::context_app::open(app, client);
                    crate::telemetry::slash_command_clicked(app, "/context", "footer");
                }
            }
        }
        _ => {}
    }
}

fn selection_event(app: &mut App, client: &Arc<Client>, target: MouseTarget, event: MouseEvent) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            // The input box stays enabled while the list is focused, so
            // clicking the composer hands it focus.
            if target == MouseTarget::Composer && app.subagents.list.focused {
                crate::subagents::focus_input(app);
            }
            if target == MouseTarget::Composer {
                crate::rail::leave(app);
            }
            // The inline notice survives a transcript mouse-down
            // (it clears only via its own timeout or a queue-mode event).
            app.selection.had_selection_at_press =
                app.selection.region.is_some() || app.selection.bottom_bar.is_some();
            // A bottom app hides the composer but leaves its last rect behind, so
            // only a press routed to the painted composer may select in it.
            if target == MouseTarget::Composer {
                selection::press(app, at);
            } else {
                selection::press_owned(app, at, selection::RegionId::Main);
            }
        }
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            // The press cell and drag flag are cleared by release; read them first.
            let pressed = app.selection.press;
            let dragged = app.selection.dragged;
            let released = selection::release(app);
            // A plain click on the model name opens `/model` and keeps the composer's draft.
            let on_model = |at| crate::ui::composer_title::model_at(app, at);
            if target == MouseTarget::Composer
                && !dragged
                && !matches!(released, Release::Selected)
                && pressed.is_some_and(on_model)
                && on_model(at)
            {
                // The second press of a double click starts afresh instead of selecting a word.
                app.selection.chain = Default::default();
                model_picker::open(app, client);
                crate::telemetry::slash_command_clicked(app, "/model", "composer");
                return;
            }
            if target != MouseTarget::Transcript || matches!(released, Release::Selected) {
                return;
            }
            let link = app.view.link_hitmap.iter().find(|link| link.contains(at));
            if let Some(link) = link.cloned() {
                open_link(app, &link);
            } else if matches!(released, Release::RegionClick) {
                // A plain click on the resting cat replays its animation round,
                // unless the user disabled the animation; the click stays a no-op.
                if app
                    .view
                    .banner_cat_area
                    .is_some_and(|area| area.contains((event.column, event.row).into()))
                {
                    if !app.session.startup_config.disable_welcome_banner_animation {
                        app.view.banner.restart();
                    }
                } else {
                    toggle_effect_at(app, event.row);
                }
            }
        }
        _ => {}
    }
}

/// Open a clicked markdown link: a URL in the browser, a file, or an inline image.
pub fn open_link(app: &mut App, link: &crate::ui::markdown::Link) {
    match link.kind() {
        crate::ui::markdown::LinkKind::External => external_url::open(link.target()),
        crate::ui::markdown::LinkKind::Attachment => external_url::open_file(link.target()),
        crate::ui::markdown::LinkKind::InlineImage => {
            crate::inline_images::open(app, link.target())
        }
    }
}

/// Toggle the entry or group a transcript row hit, following the viewed
/// child's transcript and cache when a sub-agent view is open.
pub fn toggle_effect_at(app: &mut App, row: u16) {
    let Some((header, id)) = app
        .view
        .entry_hitmap
        .iter()
        .find(|(top, bottom, _, _)| row >= *top && row < *bottom)
        .map(|(_, _, header, id)| (*header, id.clone()))
    else {
        return;
    };
    // With a selection showing, only the header row toggles.
    if app.selection.had_selection_at_press && header != Some(row) {
        return;
    }
    // The child view swaps its transcript and cache in only at render time,
    // so the fold and the layout cache to invalidate both follow the viewed
    // child, like `App::toggle_tools`.
    let expanded = &mut app.view.expanded;
    let reveal = if let Some(child) = app
        .subagents
        .viewed_subagent_id
        .as_deref()
        .and_then(|child_id| app.subagents.transcripts.child_mut(child_id))
    {
        let Some(reveal) = child.transcript.toggle_fold(expanded, &id) else {
            return;
        };
        child.cache.invalidate_layouts();
        reveal
    } else {
        let Some(reveal) = app.view.transcript.toggle_fold(expanded, &id) else {
            return;
        };
        app.view.transcript_cache.invalidate_layouts();
        reveal
    };
    // A toggling click ends the chain, so the next click toggles again instead of selecting a word.
    app.selection.chain = Default::default();
    anchor_toggle(app, row, id, reveal);
}

/// Hold the clicked entry steady; an expansion then scrolls just enough to show what it revealed.
fn anchor_toggle(app: &mut App, row: u16, key: String, reveal: bool) {
    let landing = row.saturating_sub(app.view.selection_region.area.y);
    let clicked = i32::from(landing);
    app.view.scroll_anchor = app
        .view
        .entry_rows
        .iter()
        .find(|(_, top, height)| (*top..*top + i32::from(*height)).contains(&clicked))
        .map(|&(index, row, height)| ScrollAnchor {
            index,
            row,
            height,
            landing,
            inset: app.view.sticky_prompt.map_or(0, |(_, rows)| rows),
            key: Some(key),
            reveal,
        });
}
