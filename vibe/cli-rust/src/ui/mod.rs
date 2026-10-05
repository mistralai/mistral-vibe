//! UI components; each submodule owns one region of the screen.

pub mod approval;
pub mod auth_app;
pub mod banner;
pub mod bottom_bar;
pub mod braille_renderer;
pub mod chat_input;
pub mod completion_popup;
pub(crate) mod composer_layout;
pub mod config;
pub mod config_edit;
mod config_options;
pub mod connector_auth;
pub mod feedback_bar;
pub mod highlight;
pub mod loading;
pub mod log_level_picker;
pub mod markdown;
pub mod mcp;
pub mod mcp_oauth;
pub mod model_picker;
pub mod narrator;
pub mod notice;
pub mod pulse;
pub mod question_app;
mod question_layout;
mod question_rows;
pub mod recording_indicator;
pub mod resume_picker;
pub mod rewind;
pub mod rng;
pub mod scrollbar;
pub mod selection;
mod styled_text;
pub mod subagent_list;
pub mod tab_cells;
pub mod theme;
pub mod theme_picker;
pub mod thinking_picker;
pub mod toast;
pub mod todo;
pub mod transcript;
pub mod trust_folders;
mod trust_folders_layout;
mod trust_folders_paint;
pub(crate) mod trust_folders_selection;
mod trust_folders_text;
pub mod vibe_code_project;
pub mod voice_app;

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::Frame;

use crate::app::App;

/// Lay out the screen and draw every component. Constraints mirror the Python
pub fn draw(app: &mut App, f: &mut Frame) {
    app.chat_input.sync_mentions();
    let area = f.area();
    draw_active_screen(app, f, area);
    // The toast stack is a top-level overlay so warnings show on every screen,
    // not just the base chat (Python `ToastRack` floats over the whole app).
    toast::draw(app, f, toast_anchor(app, area));
}

/// Anchor the toast stack above the input box when it exists, else near the
/// bottom of the frame so startup warnings on modal screens stay visible.
fn toast_anchor(app: &App, area: ratatui::layout::Rect) -> ratatui::layout::Rect {
    if app.view.input_area.height > 0 {
        app.view.input_area
    } else if app.subagents.list.area.height > 0 {
        // A child view hides the input box; the list sits where it was.
        app.subagents.list.area
    } else {
        ratatui::layout::Rect::new(0, area.height.saturating_sub(1), area.width, 0)
    }
}

fn draw_active_screen(app: &mut App, f: &mut Frame, area: ratatui::layout::Rect) {
    // The trust gate runs before the session, so it owns the whole screen.
    if app.trust.open {
        trust_folders::draw(app, f, area);
        return;
    }
    // Docked, not an overlay: reserving the column first lets bottom-apps reflow into the rest.
    let (main, sidebar) = todo::split(app, area);
    app.todo_sidebar.visible = sidebar.is_some();
    draw_session_screen(app, f, main);
    if let Some(sidebar) = sidebar {
        f.buffer_mut().set_style(sidebar, theme::screen_style());
        todo::draw_sidebar(app, f, sidebar);
    }
}

/// The stack every bottom-app shares as `[transcript, loading, box, bottom_bar, todo_row]`: the row sits above the box, but last here so the shared indices hold.
pub(crate) fn bottom_app_chunks(
    app: &App,
    area: ratatui::layout::Rect,
    loading_height: u16,
    box_height: u16,
) -> [ratatui::layout::Rect; 5] {
    let chunks = Layout::vertical([
        Constraint::Min(1),
        Constraint::Length(loading_height),
        Constraint::Length(todo::row_height(app)),
        Constraint::Length(box_height),
        Constraint::Length(1),
    ])
    .split(area);
    [chunks[0], chunks[1], chunks[3], chunks[4], chunks[2]]
}

fn draw_session_screen(app: &mut App, f: &mut Frame, area: ratatui::layout::Rect) {
    // Approval callbacks block the server and take the input box before pickers.
    if app.approval.open {
        approval::draw(app, f, area);
        return;
    }
    // `ask_user_question` open: the server is blocked on the answer, so this
    // bottom-app takes the input box before any user-opened picker can.
    if app.question_app.open {
        question_app::draw(app, f, area);
        return;
    }
    if app.voice_app.open {
        voice_app::draw(app, f, area);
        return;
    }
    // `/theme` picker open: it replaces the input box with a taller bottom-app,
    // reflowing the content above rather than overlaying a frozen base.
    if app.theme_picker.open {
        theme_picker::draw(app, f, area);
        return;
    }
    // `/model` picker open: like the theme picker, it replaces the input box.
    if app.model_picker.open {
        model_picker::draw(app, f, area);
        return;
    }
    // `/log-level` picker open: same bottom-app slot as the other pickers.
    if app.log_level_picker.open {
        log_level_picker::draw(app, f, area);
        return;
    }
    if app.thinking_picker.open {
        thinking_picker::draw(app, f, area);
        return;
    }
    if app.vibe_code_project.open {
        vibe_code_project::draw(app, f, area);
        return;
    }
    if app.resume_picker.open {
        resume_picker::draw(app, f, area);
        return;
    }
    // Rewind mode open: the panel replaces the input box while the highlighted
    // user message stays visible in the transcript above.
    if app.rewind.open {
        rewind::draw(app, f, area);
        return;
    }
    // `/mcp` browser open: it replaces the input box like the other bottom-apps.
    if app.mcp.open {
        mcp::draw(app, f, area);
        return;
    }
    // The MCP OAuth app takes the input box while a server login runs.
    if app.mcp_oauth.open {
        mcp_oauth::draw(app, f, area);
        return;
    }
    // Same for the connector auth app.
    if app.connector_auth.open {
        connector_auth::draw(app, f, area);
        return;
    }
    draw_base(app, f, area);
    if app.config_screen.open {
        config::draw(app, f, area);
    }
}

/// Input-box height: rendered chat rows, capped near 50vh.
pub fn input_box_height(app: &App, area_width: u16, area_height: u16) -> u16 {
    let lines = chat_input::content_height(app, area_width);
    let max_content = (area_height / 2).max(3);
    lines.clamp(3, max_content) + 2
}

/// Render the main chat UI (chat, loading, popup, todo, input, footer).
fn draw_base(app: &mut App, f: &mut Frame, area: ratatui::layout::Rect) {
    // Paint the theme background on every cell first, since widgets set only fg.
    f.buffer_mut().set_style(area, theme::screen_style());

    // `#loading-area`: 3 rows on a fresh session (narrator/hint), else 2.
    let loading_height = loading_height(app);
    // The shared slash-command/file popup sits above the input box.
    completion_popup::reconcile_scroll(app, area.width);
    let popup_height = completion_popup::popup_height(app, area.width);
    // Python `set_subagent_view`: the input box is hidden while a child is viewed.
    let input_height = if app.subagents.viewed_subagent_id.is_some() {
        0
    } else {
        input_box_height(app, area.width, area.height)
    };
    let chunks = Layout::vertical([
        Constraint::Min(1),                             // #chat — height: 1fr
        Constraint::Length(loading_height),             // #loading-area — height: auto
        Constraint::Length(popup_height),               // #completion-popup — height: auto, max 12
        Constraint::Length(todo::row_height(app)),      // pinned todo line — height: 1
        Constraint::Length(input_height),               // #input-box (grows with rendered rows)
        Constraint::Length(subagent_list::height(app)), // #subagent-list — under the input
        Constraint::Length(1),                          // #bottom-bar — height: auto
    ])
    .split(area);

    app.view.input_area = chunks[4];
    // A child view swaps the rendered transcript (Python hides the messages
    // area and mounts the child's transcript in the same scroll region). The
    // render cache and the last measured height belong to the transcript:
    // their revision streams are independent, so both must follow the swap.
    let viewed_child = app.subagents.viewed_subagent_id.clone();
    let mut swapped = false;
    if let Some(child_id) = &viewed_child {
        if let Some(child) = app.subagents.transcripts.child_mut(child_id) {
            std::mem::swap(&mut app.view.transcript, &mut child.transcript);
            std::mem::swap(&mut app.view.transcript_cache, &mut child.cache);
            std::mem::swap(&mut app.view.last_total, &mut child.last_total);
            swapped = true;
        }
    }
    transcript::draw(app, f, chunks[0]);
    if swapped {
        let child_id = viewed_child.clone().unwrap_or_default();
        if let Some(child) = app.subagents.transcripts.child_mut(&child_id) {
            std::mem::swap(&mut app.view.transcript, &mut child.transcript);
            std::mem::swap(&mut app.view.transcript_cache, &mut child.cache);
            std::mem::swap(&mut app.view.last_total, &mut child.last_total);
        }
        // Python stacks the placeholder above the transcript content, which is
        // bottom-anchored: above the first rendered row. With no rendered rows
        // the placeholder is part of the document under the banner
        // (transcript::draw), never an overlay at the area bottom.
        if let Some(placeholder) = app
            .subagents
            .transcripts
            .child(&child_id)
            .and_then(|child| child.placeholder())
        {
            if let Some(top) = app.view.entry_hitmap.first().map(|(top, _, _, _)| *top) {
                if top > chunks[0].y {
                    let row = Rect {
                        y: top - 1,
                        width: chunks[0].width.saturating_sub(1),
                        x: chunks[0].x + 1,
                        height: 1,
                    };
                    f.render_widget(
                        ratatui::widgets::Paragraph::new(placeholder).style(theme::muted_style()),
                        row,
                    );
                }
            }
        }
    }
    if popup_height > 0 {
        crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Completion);
        completion_popup::draw(app, f, chunks[2]);
    }
    todo::draw_row(app, f, chunks[3]);
    if input_height > 0 {
        crate::mouse::register_region(app, chunks[4], crate::mouse::MouseTarget::Composer);
        chat_input::draw(app, f, chunks[4]);
    }
    subagent_list::draw(app, f, chunks[5]);
    bottom_bar::draw(app, f, chunks[6]);
    selection::overlay(app, f);
    draw_loading_area(app, f, chunks[1]);
    selection::loading_region(app, f, chunks[1]);
}

/// Python's `#loading-area` row, mounted on every screen that keeps it: the
/// narrator status, the loading spinner, the inline notice and the feedback
/// bar, laid out left to right.
pub(crate) fn draw_loading_area(app: &mut App, f: &mut Frame, area: ratatui::layout::Rect) {
    let notice_width = notice::width(app);
    let feedback_width = feedback_bar::width(app);
    // Python's `#loading-area` lays the narrator row out before the loading
    // spinner; zero width while idle keeps the split identical to before.
    let loading_chunks = Layout::horizontal([
        Constraint::Length(narrator::width(app)),
        Constraint::Fill(1),
        Constraint::Length(notice_width),
        Constraint::Length(u16::from(feedback_width > 0)),
        Constraint::Length(feedback_width),
    ])
    .split(area);

    narrator::draw(app, f, loading_chunks[0]);
    loading::draw(app, f, loading_chunks[1]);
    feedback_bar::draw(app, f, loading_chunks[4]);
    notice::draw(app, f, loading_chunks[2]);
}

fn loading_height(app: &App) -> u16 {
    if app.view.transcript.is_empty() {
        3
    } else {
        2
    }
}
