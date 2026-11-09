//! SGR-pixels mouse reports (DECSET 1016) for sub-cell scrollbar drags.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Mutex, PoisonError};

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
/// Whether the TUI captures the mouse; pixel reports stay off while it does not.
static CAPTURE: AtomicBool = AtomicBool::new(true);
/// The packed `Grid` pixel reports would use, kept while a gesture holds cell reports.
static VALID: AtomicU64 = AtomicU64::new(0);
/// A gesture holds cell reports: terminals clamp them, so no negative release is dropped by crossterm.
static HELD: AtomicBool = AtomicBool::new(false);
/// A report-unit switch the terminal may not have applied yet, and the last pointer cell.
struct Settling {
    /// The pixel grid and whether the switch goes to pixels, until a report arrives in the new units.
    switch: Option<(Grid, bool)>,
    /// Reports still read by nearness before the new units are assumed.
    budget: u8,
    last: (u16, u16),
}
static SETTLING: Mutex<Settling> = Mutex::new(Settling {
    switch: None,
    budget: 0,
    last: (0, 0),
});
/// Reports a switch may leave in flight; bounds a misread when both units land near the top-left.
const IN_FLIGHT: u8 = 64;

/// The terminal's text area, in cells and in pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Grid {
    pub columns: u16,
    pub rows: u16,
    pub width: u16,
    pub height: u16,
}

impl Grid {
    /// The cells' pixel area: VS Code's xterm.js reports just the cells, others add the leftover past the last one.
    pub fn new(columns: u16, rows: u16, width: u16, height: u16, exact: bool) -> Option<Self> {
        let cells = |pixels: u16, count: u16| {
            if exact {
                pixels
            } else {
                pixels / count.max(1) * count
            }
        };
        let grid = Self {
            columns,
            rows,
            width: cells(width, columns),
            height: cells(height, rows),
        };
        (grid.columns > 0 && grid.rows > 0 && grid.width > 0 && grid.height > 0).then_some(grid)
    }

    fn current() -> Option<Self> {
        let size = crossterm::terminal::window_size().ok()?;
        let exact = crate::terminal_detect::is_vscode_family(crate::terminal_detect::detect());
        Self::new(size.columns, size.rows, size.width, size.height, exact)
    }

    /// Cell size in pixels, fractional on hosts that scale cells (VS Code's xterm.js).
    pub fn cell(self) -> (f64, f64) {
        (
            f64::from(self.width) / f64::from(self.columns),
            f64::from(self.height) / f64::from(self.rows),
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

/// Follow mouse capture: `sync` keeps pixel reports off while the mouse is not captured.
pub fn set_capture(on: bool) {
    CAPTURE.store(on, Ordering::Relaxed);
}

/// Match pixel reports to the current window; the sequence the caller must send, if any.
pub fn sync() -> Option<&'static str> {
    let packed = CELL.load(Ordering::Relaxed);
    let cell = (packed != 0).then_some(((packed >> 16) as u16, packed as u16));
    resync(validated(cell, Grid::current()))
}

/// Match pixel reports to `grid`, the validated window grid if any; the sequence to send, if any.
fn resync(grid: Option<Grid>) -> Option<&'static str> {
    VALID.store(grid.map_or(0, Grid::pack), Ordering::Relaxed);
    let (held, capture) = (
        HELD.load(Ordering::Relaxed),
        CAPTURE.load(Ordering::Relaxed),
    );
    switch(reported_grid(grid, held, capture))
}

/// Hold cell reports for a gesture, or go back to pixel reports after it; the sequence to send, if any.
pub fn hold_cells(held: bool) -> Option<&'static str> {
    let Some(grid) = Grid::unpack(VALID.load(Ordering::Relaxed)) else {
        // A resize can invalidate the grid mid-gesture: its end must still let pixel reports back.
        if !held && HELD.load(Ordering::Relaxed) {
            HELD.store(false, Ordering::Relaxed);
        }
        return None;
    };
    if HELD.swap(held, Ordering::Relaxed) == held {
        return None;
    }
    switch(reported_grid(
        Some(grid),
        held,
        CAPTURE.load(Ordering::Relaxed),
    ))
}

/// Turn pixel reports on over `grid`, or off, reading the reports still in flight by nearness.
fn switch(grid: Option<Grid>) -> Option<&'static str> {
    let previous = Grid::unpack(GRID.swap(grid.map_or(0, Grid::pack), Ordering::Relaxed));
    let sequence = transition(previous.is_some(), grid.is_some());
    if let Some(pixels) = grid.or(previous).filter(|_| sequence.is_some()) {
        let mut settling = SETTLING.lock().unwrap_or_else(PoisonError::into_inner);
        settling.switch = Some((pixels, grid.is_some()));
        settling.budget = IN_FLIGHT;
    }
    sequence
}

/// The grid pixel reports use: the validated window, unless a gesture holds cells or the mouse is not captured.
pub fn reported_grid(valid: Option<Grid>, held: bool, capture: bool) -> Option<Grid> {
    valid.filter(|_| !held && capture)
}

/// The sequence that moves mouse reports from pixels `was_on` to pixels `on`, if they differ.
pub fn transition(was_on: bool, on: bool) -> Option<&'static str> {
    match (was_on, on) {
        (false, true) => Some(ENABLE),
        (true, false) => Some(CELLS),
        _ => None,
    }
}

/// `grid` when its cells round to the probed cell size (else another scale or a zoom).
pub fn validated(cell: Option<(u16, u16)>, grid: Option<Grid>) -> Option<Grid> {
    let cell = cell?;
    let probed = (f64::from(cell.0), f64::from(cell.1));
    grid.filter(|grid| {
        let (width, height) = grid.cell();
        (width.round(), height.round()) == probed
    })
}

/// Stop converting pixel reports; true when the caller must send `DISABLE`.
pub fn disable() -> bool {
    HELD.store(false, Ordering::Relaxed);
    SETTLING
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .switch = None;
    GRID.swap(0, Ordering::Relaxed) != 0
}

pub fn grid() -> Option<Grid> {
    Grid::unpack(GRID.load(Ordering::Relaxed))
}

/// The cell event for a terminal mouse event, and its pointer row in fractional cells.
pub fn to_cells(event: MouseEvent) -> (MouseEvent, f64) {
    if VALID.load(Ordering::Relaxed) == 0 {
        return cells(grid(), event);
    }
    let mut settling = SETTLING.lock().unwrap_or_else(PoisonError::into_inner);
    let converted = match settling.switch.filter(|_| settling.budget > 0) {
        Some((pixels, to_pixels)) => {
            settling.budget -= 1;
            let (converted, pixel) = settle(pixels, settling.last, event, to_pixels);
            if pixel == to_pixels {
                // The terminal switched: every later report is in the new units.
                settling.switch = None;
            }
            converted
        }
        None => cells(grid(), event),
    };
    settling.last = (converted.0.column, converted.0.row);
    converted
}

/// Read a report sent around a switch in the units nearer the last cell (ties: new units); true for pixels.
pub fn settle(
    grid: Grid,
    last: (u16, u16),
    event: MouseEvent,
    to_pixels: bool,
) -> ((MouseEvent, f64), bool) {
    let distance =
        |(cell, _): &(MouseEvent, f64)| cell.column.abs_diff(last.0).max(cell.row.abs_diff(last.1));
    let as_pixels = cells(Some(grid), event);
    let as_cells = cells(None, event);
    let (pixel, cell) = (distance(&as_pixels), distance(&as_cells));
    if pixel < cell || (pixel == cell && to_pixels) {
        (as_pixels, true)
    } else {
        (as_cells, false)
    }
}

/// The cell event for a mouse event reported over `grid`'s pixels, or cells without a grid.
pub fn cells(grid: Option<Grid>, event: MouseEvent) -> (MouseEvent, f64) {
    let Some(grid) = grid else {
        return (event, f64::from(event.row));
    };
    // Undo crossterm's 1-based SGR adjustment: pixels are 0-based (pixel 0 wraps to `u16::MAX`).
    let pixel = |value: u16| f64::from(value.wrapping_add(1));
    let (cell_width, cell_height) = grid.cell();
    let column = pixel(event.column) / cell_width;
    let row = pixel(event.row) / cell_height;
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

#[cfg(test)]
pub(crate) mod tests;
