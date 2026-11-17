//! The idle loading row: when the session's next scheduled loop runs, and its title, after the still clock.

use ratatui::text::Span;
use unicode_width::UnicodeWidthStr;

use crate::app::App;
use crate::loops::schedule;
use crate::ui::hint_line;
use crate::utils::text::ellipsize;

/// Fewest title cells worth showing; a narrower row keeps only the delay.
const MIN_TITLE_WIDTH: usize = 8;

/// `◷ next loop in 5m: Build watch`, cut to `width`; `None` without loops.
pub(super) fn next_loop(app: &App, width: u16) -> Option<Vec<Span<'static>>> {
    // An open picker or app owns the screen; the composer's idle row comes back with it.
    if app.focus() != crate::focus::Focus::Composer {
        return None;
    }
    let next = app.session.loops.first()?;
    let left = next.next_fire_at - crate::clock::unix_now_seconds();
    let delay = match left <= 0.0 {
        true => "next loop runs now".to_owned(),
        false => format!("next loop {}", schedule::next_run(left)),
    };
    let title = schedule::title(next);
    let clock = format!("{} ", super::WAITING_GLYPH);
    let width = usize::from(width).saturating_sub(clock.width());
    let room = width.saturating_sub(delay.width() + 2);
    let text = match room >= MIN_TITLE_WIDTH.min(title.width()) && !title.is_empty() {
        true => format!("{delay}: {}", ellipsize(title, room)),
        false => ellipsize(&delay, width),
    };
    let muted = hint_line::action_style();
    Some(vec![Span::styled(clock, muted), Span::styled(text, muted)])
}
