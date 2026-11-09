use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use super::*;

/// A 100x40 window of 10x20-pixel cells.
pub(crate) const GRID_10X20: Grid = Grid {
    columns: 100,
    rows: 40,
    width: 1000,
    height: 800,
};

/// Serializes the crate tests sharing the report-mode globals.
static LOCK: Mutex<()> = Mutex::new(());

/// Lock the report-mode globals and reset them, with pixel reports over `GRID_10X20` on or unsupported.
pub(crate) fn reset(pixels: bool) -> std::sync::MutexGuard<'static, ()> {
    let guard = LOCK.lock().unwrap_or_else(PoisonError::into_inner);
    let packed = if pixels { GRID_10X20.pack() } else { 0 };
    VALID.store(packed, Ordering::Relaxed);
    GRID.store(packed, Ordering::Relaxed);
    HELD.store(false, Ordering::Relaxed);
    CAPTURE.store(true, Ordering::Relaxed);
    *SETTLING.lock().unwrap_or_else(PoisonError::into_inner) = Settling {
        switch: None,
        budget: 0,
        last: (0, 0),
    };
    guard
}

/// A report as crossterm parses it: 1-based values minus one.
fn report(kind: MouseEventKind, x: u16, y: u16) -> MouseEvent {
    MouseEvent {
        kind,
        column: x - 1,
        row: y - 1,
        modifiers: KeyModifiers::NONE,
    }
}

fn cell_of(kind: MouseEventKind, x: u16, y: u16) -> (u16, u16) {
    let (cell, _) = to_cells(report(kind, x, y));
    (cell.column, cell.row)
}

const DOWN: MouseEventKind = MouseEventKind::Down(MouseButton::Left);
const DRAG: MouseEventKind = MouseEventKind::Drag(MouseButton::Left);
const UP: MouseEventKind = MouseEventKind::Up(MouseButton::Left);

#[test]
fn a_gesture_holds_cell_reports_until_it_ends() {
    let _guard = reset(true);

    assert_eq!(hold_cells(true), Some(CELLS));
    assert_eq!(grid(), None);
    assert_eq!(hold_cells(true), None);
    assert_eq!(hold_cells(false), Some(ENABLE));
    assert_eq!(grid(), Some(GRID_10X20));
    assert_eq!(hold_cells(false), None);
}

#[test]
fn a_gesture_ending_after_a_resize_invalidated_its_grid_lets_pixel_reports_back() {
    let _guard = reset(true);
    assert_eq!(hold_cells(true), Some(CELLS));
    assert_eq!(resync(None), None);

    assert_eq!(hold_cells(false), None);

    assert_eq!(resync(Some(GRID_10X20)), Some(ENABLE));
    assert_eq!(grid(), Some(GRID_10X20));
}

#[test]
fn with_mouse_capture_off_no_gesture_end_or_resync_enables_pixel_reports() {
    let _guard = reset(true);
    assert_eq!(hold_cells(true), Some(CELLS));
    // The guard turning capture off mid-gesture.
    set_capture(false);
    assert!(!disable());

    // Keys and focus still end gestures and resizes still resync, with no mouse captured.
    assert_eq!(hold_cells(false), None);
    assert_eq!(hold_cells(true), None);
    assert_eq!(hold_cells(false), None);
    assert_eq!(resync(Some(GRID_10X20)), None);
    assert_eq!(grid(), None);

    // Turning capture back on goes through the same switch, with settling armed.
    set_capture(true);
    assert_eq!(resync(Some(GRID_10X20)), Some(ENABLE));
    assert_eq!(grid(), Some(GRID_10X20));
    let settling = SETTLING.lock().unwrap_or_else(PoisonError::into_inner);
    assert_eq!(settling.switch, Some((GRID_10X20, true)));
}

#[test]
fn without_pixel_reports_a_gesture_sends_nothing() {
    let _guard = reset(false);

    assert_eq!(hold_cells(true), None);
    assert_eq!(hold_cells(false), None);
    assert_eq!(grid(), None);
    assert!(!HELD.load(Ordering::Relaxed));
}

#[test]
fn pixel_reports_in_flight_after_a_hold_convert_until_a_cell_report_lands() {
    let _guard = reset(true);
    assert_eq!(cell_of(DOWN, 505, 410), (50, 20));
    hold_cells(true);

    assert_eq!(cell_of(DRAG, 525, 410), (52, 20));
    assert_eq!(cell_of(DRAG, 54, 21), (53, 20));
    // That cell report confirmed the switch: no later report converts from pixels.
    assert_eq!(cell_of(DRAG, 526, 411), (525, 410));
}

#[test]
fn cell_reports_in_flight_after_a_release_stay_cells_until_a_pixel_report_lands() {
    let _guard = reset(true);
    cell_of(DOWN, 505, 410);
    hold_cells(true);
    cell_of(DRAG, 52, 21);
    assert_eq!(cell_of(UP, 53, 21), (52, 20));
    hold_cells(false);

    assert_eq!(cell_of(MouseEventKind::Moved, 54, 21), (53, 20));
    assert_eq!(cell_of(MouseEventKind::Moved, 545, 410), (54, 20));
    // That pixel report confirmed the switch: small values are pixels again.
    assert_eq!(cell_of(MouseEventKind::Moved, 6, 2), (0, 0));
}

#[test]
fn a_release_at_the_top_edge_reads_as_its_cell() {
    let _guard = reset(true);
    cell_of(DOWN, 505, 410);
    hold_cells(true);
    cell_of(DRAG, 50, 2);

    assert_eq!(cell_of(UP, 50, 1), (49, 0));
    assert_eq!(hold_cells(false), Some(ENABLE));
}

#[test]
fn reports_ambiguous_near_the_top_left_take_the_new_units_once_the_budget_runs_out() {
    let _guard = reset(true);
    assert_eq!(cell_of(DOWN, 5, 5), (0, 0));
    hold_cells(true);

    for _ in 0..IN_FLIGHT {
        assert_eq!(cell_of(DRAG, 2, 1), (0, 0));
    }

    assert_eq!(cell_of(DRAG, 2, 1), (1, 0));
}
