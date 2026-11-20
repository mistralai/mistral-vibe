//! Bottom-bar fitting: each segment's forms, widest first, and the order they shrink in.

use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use super::footer_gauge::{context_forms, usage_forms, Usage};

/// Between the gauges.
pub const GAUGE_SEP: &str = " · ";
/// Follows a shortened cwd; a click copies the whole path.
pub const COPY_ICON: &str = "⧉";
/// The cwd shrinks to `…/` and its last component, at most this many cells, once the gauges are levels.
const CWD_FLOOR: usize = 16;

/// What a footer part means, mapped to a style by the renderer.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tone {
    Muted,
    /// Plain text color: the copy icon.
    Text,
    /// A gauge's fill on its track, then the same nearing and at the end of the allowance.
    Gauge,
    GaugeWarn,
    GaugeAlarm,
}

/// One rendering of a segment: styled parts painted side by side.
pub type Form = Vec<(String, Tone)>;

#[derive(Clone, Copy)]
enum Slot {
    Usage,
    Context,
}

impl Slot {
    /// The segment's position in `fit`'s array.
    fn index(self) -> usize {
        match self {
            Slot::Usage => 0,
            Slot::Context => 1,
        }
    }
}

/// Each step moves one segment to its next narrower form; a segment past its last form is hidden.
/// The gauges turn from bars to one-cell levels, then lose their numbers, but never hide.
const LADDER: [Slot; 4] = [Slot::Context, Slot::Usage, Slot::Context, Slot::Usage];
/// The steps taken before the cwd shortens: the gauges turn into levels.
const BEFORE_CWD: usize = 2;

pub struct Inputs<'a> {
    pub width: usize,
    pub cwd: &'a str,
    /// Whether the copy icon may follow the cwd once it is shortened.
    pub copy_icon: bool,
    /// The quit hint's width; it replaces the cwd and never shrinks.
    pub quit: Option<usize>,
    pub tokens: (u64, u64),
    pub usage: Option<Usage>,
}

/// The chosen forms; an empty form or string is hidden.
#[derive(Debug, Default)]
pub struct Fitted {
    pub cwd: String,
    /// Paint the copy icon after the shortened cwd.
    pub copy_icon: bool,
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
        Segment::new(usage_forms(inputs.usage)),
        Segment::new(context_forms(inputs.tokens)),
    ];
    let cwd_full = inputs.quit.unwrap_or_else(|| inputs.cwd.width());
    let floor = if quit {
        cwd_full
    } else {
        cwd_full.min(cwd_floor(inputs.cwd))
    };
    // The icon and its leading space show only while the cwd is shortened.
    let icon = if inputs.copy_icon && !quit {
        1 + COPY_ICON.width()
    } else {
        0
    };
    let shortened = |cells: usize| cells > 0 && cells < cwd_full;
    let cwd = |cells: usize| cells + if shortened(cells) { icon } else { 0 };
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
    let [usage, context] = segments.map(|segment| segment.current());
    Fitted {
        cwd: if quit {
            String::new()
        } else {
            elide_path(inputs.cwd, cwd_width)
        },
        copy_icon: icon > 0 && shortened(cwd_width),
        context,
        usage,
    }
}

/// The whole row's cells with a `cwd`-cell cwd: left side, one-cell spacer, right side.
fn total(segments: &[Segment; 2], cwd: usize) -> usize {
    let [usage, context] = segments.each_ref().map(Segment::width);
    let sep = GAUGE_SEP.width() * usize::from(usage > 0 && context > 0);
    let right = usage + context + sep;
    cwd + right + usize::from(cwd > 0 && right > 0)
}

/// The cwd's narrowest clean form, `…/` and its last component, in cells.
fn cwd_floor(cwd: &str) -> usize {
    let tail = cwd
        .rfind(['/', '\\'])
        .map_or(cwd.width(), |at| 1 + cwd[at..].width());
    tail.min(CWD_FLOOR)
}

/// Fit `path` in `width` cells keeping its end: `…` and the last whole words or components, else `…` and the tail.
pub fn elide_path(path: &str, width: usize) -> String {
    if path.width() <= width {
        return path.to_owned();
    }
    if width == 0 {
        return String::new();
    }
    let cuts = path.char_indices().filter_map(|(at, c)| match c {
        '/' | '\\' => Some(at),
        '-' | '_' | ' ' => Some(at + 1),
        _ => None,
    });
    if let Some(at) = cuts
        .filter(|&at| at < path.len())
        .find(|&at| path[at..].width() < width)
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
