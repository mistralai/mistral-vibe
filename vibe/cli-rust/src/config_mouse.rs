//! Mouse hit testing for `/config` rows.

use crate::app::App;
use crate::config;

pub fn row_at(app: &App, column: u16, row: u16) -> Option<usize> {
    let area = app.config_screen.area;
    let h = (area.height as u32 * 96 / 100) as u16;
    let top = area.y + (area.height.saturating_sub(h)) / 2 + 4;
    let visible = h.saturating_sub(7) as usize;
    if column < area.x || row < top || row >= top + visible as u16 {
        return None;
    }
    let fields = config::filtered(app);
    let line = app.config_screen.scroll + (row - top) as usize;
    if !app.config_screen.query.trim().is_empty() && fields.len() <= 5 {
        return (line < fields.len()).then_some(line);
    }
    let popular = fields.iter().filter(|field| field.popular).count();
    if (3..3 + popular).contains(&line) {
        return Some(line - 3);
    }
    let advanced = 3 + popular + 2 + 3;
    (advanced..advanced + fields.len().saturating_sub(popular))
        .contains(&line)
        .then_some(popular + line - advanced)
}
