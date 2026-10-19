//! SGR-pixels mouse reports (DECSET 1016) for sub-cell scrollbar drags.

use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};

use crossterm::event::{Event, KeyModifiers, MouseEvent, MouseEventKind};

pub const ENABLE: &str = "\x1b[?1016h";
pub const DISABLE: &str = "\x1b[?1016l";
/// Back to cell reports mid-session: dropping 1016 alone falls back to X10, which stops at cell 223.
pub const CELLS: &str = "\x1b[?1016l\x1b[?1006h";
/// DECRQM 1016, the cell size in report pixels (`CSI 16 t`), then DA1, which every terminal answers.
pub const QUERY: &[u8] = b"\x1b[?1016$p\x1b[16t\x1b[c";

/// The probed cell size, packed `width << 16 | height`; 0 when pixel reports are unsupported.
static CELL: AtomicU32 = AtomicU32::new(0);
/// The packed `Grid` while pixel reports are on, 0 while off.
static GRID: AtomicU64 = AtomicU64::new(0);

/// The terminal's text area, in cells and in pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Grid {
    pub columns: u16,
    pub rows: u16,
    pub width: u16,
    pub height: u16,
}

impl Grid {
    fn current() -> Option<Self> {
        let size = crossterm::terminal::window_size().ok()?;
        let grid = Self {
            columns: size.columns,
            rows: size.rows,
            width: size.width,
            height: size.height,
        };
        (grid.columns > 0 && grid.rows > 0 && grid.width > 0 && grid.height > 0).then_some(grid)
    }

    /// Whole-pixel cells: the reported area also holds the leftover pixels past the last cell.
    pub fn cell(self) -> (u16, u16) {
        (
            (self.width / self.columns).max(1),
            (self.height / self.rows).max(1),
        )
    }

    fn pack(self) -> u64 {
        [self.columns, self.rows, self.width, self.height]
            .into_iter()
            .fold(0, |packed, value| packed << 16 | u64::from(value))
    }

    fn unpack(packed: u64) -> Option<Self> {
        (packed != 0).then_some(Self {
            columns: (packed >> 48) as u16,
            rows: (packed >> 32) as u16,
            width: (packed >> 16) as u16,
            height: packed as u16,
        })
    }
}

/// Ask the terminal whether it reports SGR-pixels mouse positions, and in which pixels.
pub fn probe() {
    #[cfg(unix)]
    {
        if Grid::current().is_none() || crate::tty_query::in_multiplexer() {
            return;
        }
        if let Some((width, height)) =
            crate::tty_query::open().and_then(|mut tty| probe_tty(&mut tty))
        {
            CELL.store(
                u32::from(width) << 16 | u32::from(height),
                Ordering::Relaxed,
            );
        }
    }
}

/// The cell size in report pixels when the terminal recognizes mode 1016.
#[cfg(unix)]
pub fn probe_tty(tty: &mut std::fs::File) -> Option<(u16, u16)> {
    let response = crate::tty_query::query(tty, QUERY, is_answered)?;
    supports(&response).then(|| cell_size(&response)).flatten()
}

/// The DA1 reply (`CSI ? … c`) arrived: the replies before it came first.
pub fn is_answered(response: &[u8]) -> bool {
    response
        .rsplit(|byte| *byte == 0x1b)
        .next()
        .is_some_and(|last| last.starts_with(b"[?") && last.ends_with(b"c"))
}

/// DECRPM recognizes mode 1016 (set or reset).
pub fn supports(response: &[u8]) -> bool {
    [b"\x1b[?1016;1$y", b"\x1b[?1016;2$y"]
        .iter()
        .any(|reply| response.windows(reply.len()).any(|window| window == *reply))
}

/// The `CSI 6 ; height ; width t` reply, as `(width, height)`.
pub fn cell_size(response: &[u8]) -> Option<(u16, u16)> {
    let start = response
        .windows(4)
        .position(|window| window == b"\x1b[6;")?
        + 4;
    let reply = &response[start..];
    let reply = std::str::from_utf8(&reply[..reply.iter().position(|byte| *byte == b't')?]).ok()?;
    let (height, width) = reply.split_once(';')?;
    let size = (width.parse().ok()?, height.parse().ok()?);
    (size.0 > 0 && size.1 > 0).then_some(size)
}

/// Match pixel reports to the current window; the sequence the caller must send, if any.
pub fn sync() -> Option<&'static str> {
    let packed = CELL.load(Ordering::Relaxed);
    let cell = (packed != 0).then_some(((packed >> 16) as u16, packed as u16));
    let grid = validated(cell, Grid::current());
    let was_on = GRID.swap(grid.map_or(0, Grid::pack), Ordering::Relaxed) != 0;
    transition(was_on, grid.is_some())
}

/// The sequence that moves mouse reports from pixels `was_on` to pixels `on`, if they differ.
pub fn transition(was_on: bool, on: bool) -> Option<&'static str> {
    match (was_on, on) {
        (false, true) => Some(ENABLE),
        (true, false) => Some(CELLS),
        _ => None,
    }
}

/// `grid` when its whole-pixel cells match the probed cell size (else another scale or a zoom).
pub fn validated(cell: Option<(u16, u16)>, grid: Option<Grid>) -> Option<Grid> {
    let cell = cell?;
    grid.filter(|grid| grid.cell() == cell)
}

/// Stop converting pixel reports; true when the caller must send `DISABLE`.
pub fn disable() -> bool {
    GRID.swap(0, Ordering::Relaxed) != 0
}

pub fn grid() -> Option<Grid> {
    Grid::unpack(GRID.load(Ordering::Relaxed))
}

/// The cell event for a terminal mouse event, and its pointer row in fractional cells.
pub fn to_cells(event: MouseEvent) -> (MouseEvent, f64) {
    cells(grid(), event)
}

/// The cell event for a mouse event reported over `grid`'s pixels, or cells without a grid.
pub fn cells(grid: Option<Grid>, event: MouseEvent) -> (MouseEvent, f64) {
    let Some(grid) = grid else {
        return (event, f64::from(event.row));
    };
    // Undo crossterm's 1-based SGR adjustment: pixels are 0-based (pixel 0 wraps to `u16::MAX`).
    let pixel = |value: u16| f64::from(value.wrapping_add(1));
    let (cell_width, cell_height) = grid.cell();
    let column = pixel(event.column) / f64::from(cell_width);
    let row = pixel(event.row) / f64::from(cell_height);
    let cell = |value: f64, count: u16| (value as u16).min(count.saturating_sub(1));
    let cells = MouseEvent {
        column: cell(column, grid.columns),
        row: cell(row, grid.rows),
        ..event
    };
    (cells, row)
}

/// Drops motion reports on the previous cell, or the previous eighth of a cell for drags.
#[derive(Default)]
pub struct Coalesce(Option<(MouseEventKind, u16, u16, KeyModifiers)>);

impl Coalesce {
    pub fn keep(&mut self, grid: Option<Grid>, event: &Event) -> bool {
        let Event::Mouse(mouse) = event else {
            self.0 = None;
            return true;
        };
        let (cell, row) = cells(grid, *mouse);
        let drag = matches!(mouse.kind, MouseEventKind::Drag(_));
        let row = if drag { (row * 8.0) as u16 } else { cell.row };
        let key = (mouse.kind, cell.column, row, mouse.modifiers);
        if (drag || mouse.kind == MouseEventKind::Moved) && self.0 == Some(key) {
            return false;
        }
        self.0 = Some(key);
        true
    }
}
