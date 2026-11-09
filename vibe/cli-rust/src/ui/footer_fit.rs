//! Bottom-bar fitting: each segment's forms, widest first, and the order they shrink in.

use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use super::footer_gauge::{context_forms, usage_forms, Usage};

/// Between the left-side segments (cwd, PR/MR link).
pub const GAP: &str = "  ";
/// Between the gauges.
pub const GAUGE_SEP: &str = " · ";
/// Leads a single PR/MR, colored by its state.
pub const STATE_DOT: &str = "● ";
/// Follows the cwd; a click copies the whole path.
pub const COPY_ICON: &str = "⧉";
/// The cwd shrinks to `…/` and its last component, at most this many cells, once the gauges are levels and the PR/MR status is gone.
const CWD_FLOOR: usize = 16;

/// What a footer part means, mapped to a style by the renderer.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tone {
    Muted,
    /// Plain text color: the PR/MR label and the copy icon.
    Text,
    Good,
    Bad,
    Done,
    /// A gauge's fill on its track, then the same nearing and at the end of the allowance.
    Gauge,
    GaugeWarn,
    GaugeAlarm,
}

/// One rendering of a segment: styled parts painted side by side.
pub type Form = Vec<(String, Tone)>;

#[derive(Clone, Copy)]
enum Slot {
    Link,
    Usage,
    Context,
}

impl Slot {
    /// The segment's position in `fit`'s array.
    fn index(self) -> usize {
        match self {
            Slot::Link => 0,
            Slot::Usage => 1,
            Slot::Context => 2,
        }
    }
}

/// Each step moves one segment to its next narrower form; a segment past its last form is hidden.
/// The gauges turn from bars to one-cell levels, then lose their numbers, but never hide.
const LADDER: [Slot; 7] = [
    Slot::Context,
    Slot::Usage,
    Slot::Link,
    Slot::Link,
    Slot::Context,
    Slot::Usage,
    Slot::Link,
];
/// The steps taken before the cwd shortens: the gauges turn into levels and the PR/MR status goes.
const BEFORE_CWD: usize = 3;

/// The PR/MR link: its label (`PR #123`), short label (`#123`), synced status and the state dot's tone (`None` paints no dot).
pub struct LinkInput {
    pub label: String,
    pub short: String,
    pub status: Option<String>,
    pub dot: Option<Tone>,
}

pub struct Inputs<'a> {
    pub width: usize,
    pub cwd: &'a str,
    /// Whether the copy icon follows the cwd.
    pub copy_icon: bool,
    /// The quit hint's width; it replaces the cwd and link and never shrinks.
    pub quit: Option<usize>,
    pub link: Option<LinkInput>,
    pub tokens: (u64, u64),
    pub usage: Option<Usage>,
}

/// The chosen forms; an empty form or string is hidden.
#[derive(Debug, Default)]
pub struct Fitted {
    pub cwd: String,
    /// Paint the copy icon after the cwd.
    pub copy_icon: bool,
    pub link: Form,
    pub context: Form,
    pub usage: Form,
}

struct Segment {
    forms: Vec<Form>,
    at: usize,
}

impl Segment {
    fn new(forms: Vec<Form>) -> Self {
        Self { forms, at: 0 }
    }
    fn current(&self) -> Form {
        self.forms.get(self.at).cloned().unwrap_or_default()
    }
    fn width(&self) -> usize {
        self.forms
            .get(self.at)
            .map_or(0, |form| form.iter().map(|(text, _)| text.width()).sum())
    }
}

pub fn fit(inputs: Inputs) -> Fitted {
    let quit = inputs.quit.is_some();
    let mut segments = [
        Segment::new(if quit {
            vec![]
        } else {
            link_forms(inputs.link)
        }),
        Segment::new(usage_forms(inputs.usage)),
        Segment::new(context_forms(inputs.tokens)),
    ];
    let cwd_full = inputs.quit.unwrap_or_else(|| inputs.cwd.width());
    let floor = if quit {
        cwd_full
    } else {
        cwd_full.min(cwd_floor(inputs.cwd))
    };
    // The icon and its leading space show while any of the cwd does.
    let icon = if inputs.copy_icon && !quit {
        1 + COPY_ICON.width()
    } else {
        0
    };
    let cwd = |cells: usize| if cells == 0 { 0 } else { cells + icon };
    let mut ladder = LADDER.iter();
    for slot in ladder.by_ref().take(BEFORE_CWD) {
        if total(&segments, cwd(cwd_full)) <= inputs.width {
            break;
        }
        segments[slot.index()].at += 1;
    }
    while total(&segments, cwd(floor)) > inputs.width {
        let Some(slot) = ladder.next() else { break };
        segments[slot.index()].at += 1;
    }
    let cwd_width = (1..=cwd_full)
        .rev()
        .find(|&cells| total(&segments, cwd(cells)) <= inputs.width)
        .unwrap_or(0);
    let [link, usage, context] = segments.map(|segment| segment.current());
    Fitted {
        cwd: if quit {
            String::new()
        } else {
            elide_path(inputs.cwd, cwd_width)
        },
        copy_icon: icon > 0 && cwd_width > 0,
        link,
        context,
        usage,
    }
}

/// The whole row's cells with a `cwd`-cell cwd: left side, one-cell spacer, right side.
fn total(segments: &[Segment; 3], cwd: usize) -> usize {
    let join = |widths: &[usize], sep: usize| {
        let shown = widths.iter().filter(|&&w| w > 0).count();
        widths.iter().sum::<usize>() + sep * shown.saturating_sub(1)
    };
    let [link, usage, context] = segments.each_ref().map(Segment::width);
    let left = join(&[cwd, link], GAP.width());
    let right = join(&[usage, context], GAUGE_SEP.width());
    left + right + usize::from(left > 0 && right > 0)
}

/// `● PR #123 mergeable`, `● PR #123`, then `● #123`: the dot keeps the state visible at every width.
fn link_forms(link: Option<LinkInput>) -> Vec<Form> {
    let Some(link) = link else { return vec![] };
    let form = |text: String, status: Option<&String>| {
        let mut form: Form = link
            .dot
            .map(|tone| (STATE_DOT.to_owned(), tone))
            .into_iter()
            .collect();
        form.push((text, Tone::Text));
        form.extend(status.map(|status| (format!(" {status}"), Tone::Muted)));
        form
    };
    vec![
        form(link.label.clone(), link.status.as_ref()),
        form(link.label.clone(), None),
        form(link.short.clone(), None),
    ]
}

/// The cwd's narrowest clean form, `…/` and its last component, in cells.
fn cwd_floor(cwd: &str) -> usize {
    let tail = cwd
        .rfind(['/', '\\'])
        .map_or(cwd.width(), |at| 1 + cwd[at..].width());
    tail.min(CWD_FLOOR)
}

/// Fit `path` in `width` cells keeping its end: `…/` and the last whole components, else `…` and the tail.
pub fn elide_path(path: &str, width: usize) -> String {
    if path.width() <= width {
        return path.to_owned();
    }
    if width == 0 {
        return String::new();
    }
    if let Some((at, _)) = path
        .match_indices(['/', '\\'])
        .find(|(at, _)| path[*at..].width() < width)
    {
        return format!("…{}", &path[at..]);
    }
    let mut tail = Vec::new();
    let mut used = 1;
    for grapheme in path.graphemes(true).rev() {
        used += grapheme.width();
        if used > width {
            break;
        }
        tail.push(grapheme);
    }
    tail.reverse();
    format!("…{}", tail.concat())
}
