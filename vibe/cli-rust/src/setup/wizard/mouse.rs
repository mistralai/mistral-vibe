//! Mouse interactions for the onboarding screens: click-to-select on the
//! theme list, and clickable links and input cards. Wheel navigation stays
//! with the arrow keys (Python's wizard has no list wheel).

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;

use crate::app::App;
use crate::mouse::MouseTarget;

use super::screens;
use super::{InputCard, OnboardingState};

/// Dispatch one routed mouse event for a wizard region. Client-free: the
/// wizard runs before the app-server exists.
pub fn handle_mouse(
    app: &mut App,
    wizard: &mut OnboardingState,
    event: MouseEvent,
    target: MouseTarget,
) {
    let at = (event.column, event.row);
    match target {
        MouseTarget::OnboardingThemeList => {
            if let MouseEventKind::Down(MouseButton::Left) = event.kind {
                if let Some(index) = row_index(wizard, at) {
                    screens::select_theme(wizard, index);
                }
            }
        }
        // Like the transcript's links: a plain click (press and release on the
        // same link) opens the shared hitmap entry. A wheel over the pane or
        // its scrollbar has no click behavior.
        MouseTarget::OnboardingLinks => {
            if let MouseEventKind::Up(MouseButton::Left) = event.kind {
                if let Some(link) = app.view.link_hitmap.iter().find(|link| link.contains(at)) {
                    crate::external_url::open(link.target());
                }
            }
        }
        MouseTarget::OnboardingPreview => {}
        // Click-to-focus, like Textual's `Input`: clicking a card focuses its
        // input and places the caret at the clicked column, clamped to the
        // value. The feedback row stays change-driven, so a click between the
        // custom-domain cards never repaints it.
        MouseTarget::OnboardingInputs => {
            if let MouseEventKind::Down(MouseButton::Left) = event.kind {
                if let Some((rect, card)) = wizard
                    .input_rows
                    .iter()
                    .find(|(rect, _)| rect.contains(at.into()))
                    .copied()
                {
                    focus_input(wizard, card, at.0, rect);
                }
            }
        }
        _ => {}
    }
}

/// Focus the clicked input and move its caret to the clicked column.
fn focus_input(wizard: &mut OnboardingState, card: InputCard, column: u16, rect: Rect) {
    let input = match card {
        InputCard::Domain => {
            wizard.custom_domain_focus = 0;
            &mut wizard.custom_domain
        }
        InputCard::DomainApi => {
            wizard.custom_domain_focus = 1;
            &mut wizard.custom_domain_api
        }
        InputCard::ApiKey => &mut wizard.api_key_input,
    };
    let interior = column.saturating_sub(rect.x + 3);
    let len = input.value.chars().count() as u16;
    let index = interior.min(len) as usize;
    input.cursor = crate::utils::input_edit::char_index_to_byte_offset(&input.value, index);
}

/// The theme row under the cell, by paint-time hit target.
fn row_index(wizard: &OnboardingState, at: (u16, u16)) -> Option<usize> {
    wizard
        .theme_rows
        .iter()
        .find(|(rect, _)| rect.contains(at.into()))
        .map(|(_, index)| *index)
}
