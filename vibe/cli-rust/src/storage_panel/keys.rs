//! Keys of the panel, the details, the review and the confirmation.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use super::{actions, bulk, has_suggestion, model, Target, View};
use crate::app::App;
use crate::server::{Client, StorageRefreshParams};

pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER)
    {
        return;
    }
    if app.storage.busy.is_some() {
        return busy_key(app, key);
    }
    if app.storage.confirm.is_some() {
        return confirm_key(app, client, key);
    }
    match app.storage.view {
        View::Panel => panel_key(app, client, key),
        View::Details => details_key(app, client, key),
        View::Review => review_key(app, client, key),
    }
}

/// While a bulk archive waits for its scan, Esc stops waiting and writes the result.
fn busy_key(app: &mut App, key: KeyEvent) {
    if key.code == KeyCode::Esc {
        bulk::stop_waiting(app);
    }
}

fn confirm_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Char('y') | KeyCode::Enter => actions::confirm(app, client),
        KeyCode::Char('n') | KeyCode::Esc => app.storage.confirm = None,
        _ => {}
    }
}

fn panel_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => super::close(app),
        KeyCode::Up | KeyCode::Char('k') => step(app, false),
        KeyCode::Down | KeyCode::Char('j') => step(app, true),
        KeyCode::Enter => match super::target(app) {
            Some(Target::Suggestion) => open_review(app),
            Some(Target::Session(_)) => app.storage.view = View::Details,
            None => {}
        },
        KeyCode::Char('a') => actions::ask(app, true),
        KeyCode::Char('d') => actions::ask(app, false),
        KeyCode::Char('r') => {
            let params = StorageRefreshParams {
                full: true,
                ..Default::default()
            };
            super::refresh(app, client, params);
        }
        _ => {}
    }
}

fn details_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => app.storage.view = View::Panel,
        KeyCode::Char('a') => actions::ask(app, true),
        KeyCode::Char('d') => actions::ask(app, false),
        KeyCode::Char('r') => panel_key(app, client, key),
        _ => {}
    }
}

fn review_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let review = &mut app.storage.review;
    let len = review.order.len();
    match key.code {
        KeyCode::Esc => app.storage.view = View::Panel,
        KeyCode::Up | KeyCode::Char('k') => {
            review.list.step(false, len, true, |_| true);
        }
        KeyCode::Down | KeyCode::Char('j') => {
            review.list.step(true, len, true, |_| true);
        }
        KeyCode::Char(' ') => {
            let Some(id) = review.order.get(review.list.selected()).cloned() else {
                return;
            };
            if !review.selected.remove(&id) {
                review.selected.insert(id);
            }
        }
        KeyCode::Char('a') if review.selected.len() == len => review.selected.clear(),
        KeyCode::Char('a') => review.selected = review.order.iter().cloned().collect(),
        KeyCode::Char('s') => sort(app),
        KeyCode::Enter => bulk::start_bulk(app, client),
        _ => {}
    }
}

fn step(app: &mut App, down: bool) {
    let Some(snapshot) = app.storage.usage.snapshot.as_ref() else {
        return;
    };
    let len = snapshot.sessions.len() + usize::from(has_suggestion(&app.storage.usage));
    app.storage.list.step(down, len, true, |_| true);
}

/// Enter on the suggestion: every suggested session selected, largest first.
fn open_review(app: &mut App) {
    let Some(snapshot) = app.storage.usage.snapshot.as_ref() else {
        return;
    };
    let order = model::review_order(snapshot, false);
    let review = &mut app.storage.review;
    review.selected = order.iter().cloned().collect();
    review.order = order;
    review.by_date = false;
    review.list.reset();
    app.storage.view = View::Review;
}

/// `s`: switch between size and date, keeping the highlighted session.
fn sort(app: &mut App) {
    let Some(snapshot) = app.storage.usage.snapshot.as_ref() else {
        return;
    };
    let review = &mut app.storage.review;
    let kept = review.order.get(review.list.selected()).cloned();
    review.by_date = !review.by_date;
    review.order = model::review_order(snapshot, review.by_date);
    let index = kept
        .and_then(|id| review.order.iter().position(|other| *other == id))
        .unwrap_or(0);
    review.list.select(index);
}
