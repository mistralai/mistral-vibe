//! One session row's text: the title, the dim facts after it and the time,
//! fitted to the row's width.

use unicode_width::UnicodeWidthStr;

use crate::sessions_page::Session;
use crate::utils::text::ellipsize;

/// The mark of a row whose folder is gone from disk.
const GONE: &str = "(deleted)";

/// The dim facts after a title, most telling first: the folded count, then
/// the branch, then where the session works.
pub(super) fn extras(session: &Session, folded: usize) -> Vec<Extra> {
    let mut extras = Vec::new();
    if folded > 0 {
        extras.push(Extra::whole(format!("+{folded}")));
    }
    if let Some(branch) = session.branch() {
        extras.push(Extra::cut(branch.to_owned(), ""));
    }
    match (session.location(), session.cwd_missing) {
        (Some(location), true) => extras.push(Extra::cut(location, GONE)),
        (None, true) => extras.push(Extra::whole(GONE.to_owned())),
        (Some(location), false) => extras.push(Extra::cut(location, "")),
        (None, false) => {}
    }
    extras
}

/// A dim fact: `text` may be cut down to `MIN_EXTRA` cells, `suffix` never is.
pub(super) struct Extra {
    text: String,
    suffix: &'static str,
    cuttable: bool,
}

impl Extra {
    fn whole(text: String) -> Self {
        Self {
            text,
            suffix: "",
            cuttable: false,
        }
    }

    fn cut(text: String, suffix: &'static str) -> Self {
        Self {
            text,
            suffix,
            cuttable: true,
        }
    }

    fn render(&self, width: usize) -> String {
        let text = ellipsize(&self.text, width);
        match self.suffix {
            "" => text,
            suffix => format!("{text} {suffix}"),
        }
    }

    fn full_width(&self) -> usize {
        self.render(usize::MAX).width()
    }

    fn min_width(&self) -> usize {
        match self.cuttable {
            true => self.text.width().min(MIN_EXTRA) + self.suffix_width(),
            false => self.full_width(),
        }
    }

    fn suffix_width(&self) -> usize {
        match self.suffix {
            "" => 0,
            suffix => suffix.width() + 1,
        }
    }
}

/// The narrowest a cut extra gets before it is dropped instead.
const MIN_EXTRA: usize = 8;
/// The title keeps at least this much before extras are dropped.
const MIN_TITLE: usize = 12;
const GAP: usize = 2;

/// One row's runs, `(text, dim)`, fitting `width` cells. The time always
/// shows. The extras that fit at their narrowest are kept, in order, while
/// the title keeps `MIN_TITLE` cells. Each extra wants up to a third of the
/// row; the title gets what it needs, but never less than half of what the
/// extras do not need at their narrowest, and the extras grow into the rest.
pub(super) fn layout(
    title: &str,
    extras: &[Extra],
    time: &str,
    width: usize,
) -> Vec<(String, bool)> {
    let time_width = time.width();
    if width <= time_width + GAP {
        return vec![(ellipsize(title, width), false)];
    }
    let room = width - time_width - GAP;
    let title_floor = title.width().min(MIN_TITLE);
    let mut kept: Vec<&Extra> = Vec::new();
    let mut floors = 0;
    for extra in extras {
        let need = extra.min_width() + GAP;
        if title_floor + floors + need > room {
            break;
        }
        floors += need;
        kept.push(extra);
    }
    let cap = (width / 3).max(MIN_EXTRA);
    // The cap bounds the cuttable text; a suffix such as `(deleted)` comes on top.
    let capped = |extra: &Extra| {
        extra
            .full_width()
            .min(cap + extra.suffix_width())
            .max(extra.min_width())
    };
    let wanted: usize = kept.iter().map(|extra| capped(extra) + GAP).sum();
    let free = room - floors;
    let title_room = title.width().min(room.saturating_sub(wanted).max(free / 2));
    let mut spare = free - title_room;
    let mut shown: Vec<String> = Vec::new();
    for extra in kept {
        let floor = extra.min_width();
        let grown = (floor + spare).min(capped(extra));
        spare -= grown - floor;
        let text = extra.render(grown.saturating_sub(extra.suffix_width()));
        shown.push(text);
    }
    let title_room = room - shown.iter().map(|text| text.width() + GAP).sum::<usize>();
    let title = ellipsize(title, title_room);
    let pad = title_room - title.width();
    let mut line = vec![(title, false), (" ".repeat(pad), true)];
    for text in shown {
        line.push((" ".repeat(GAP), true));
        line.push((text, true));
    }
    line.push((" ".repeat(GAP), true));
    line.push((time.to_owned(), true));
    line
}

/// How long ago `timestamp` was, in the row's short form.
pub(super) fn relative_time(timestamp: u64) -> String {
    let now = crate::utils::replay_now_ms().unwrap_or_else(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |value| value.as_millis() as u64)
    });
    let seconds = now.saturating_sub(timestamp) / 1_000;
    match seconds {
        0..=59 => "just now".into(),
        60..=3_599 => format!("{}m ago", seconds / 60),
        3_600..=86_399 => format!("{}h ago", seconds / 3_600),
        86_400..=604_799 => format!("{}d ago", seconds / 86_400),
        _ => format!("{}w ago", seconds / 604_800),
    }
}
