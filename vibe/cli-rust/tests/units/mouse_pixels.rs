//! SGR-pixels mouse reports: support probe, pixel-to-cell conversion, motion coalescing.

use crossterm::event::{Event, MouseButton, MouseEvent, MouseEventKind};

use vibe_rs::mouse::pixels::{
    cell_size, cells, is_answered, reported_grid, settle, supports, transition, validated,
    Coalesce, Grid, ENABLE,
};

use crate::support::{down, mouse};

const GRID: Grid = Grid {
    columns: 100,
    rows: 40,
    width: 1000,
    height: 800,
};

#[test]
fn pixel_reports_map_to_their_cell_and_fractional_row() {
    let (cell, row) = cells(Some(GRID), pixels(MouseEventKind::Moved, 505, 410));

    assert_eq!((cell.column, cell.row), (50, 20));
    assert_eq!(row, 20.5);
    assert_eq!(cell.kind, MouseEventKind::Moved);
}

#[test]
fn leftover_pixels_past_the_last_cell_do_not_stretch_cells() {
    let grid = Grid::new(100, 40, 1009, 819, false).unwrap();

    let (cell, row) = cells(Some(grid), pixels(MouseEventKind::Moved, 995, 795));

    assert_eq!((cell.column, cell.row), (99, 39));
    assert_eq!(row, 39.75);
}

#[test]
fn fractional_cells_of_vscode_hosts_map_to_the_pointed_cell() {
    // VS Code's xterm.js reports exactly 100 columns of 7.83 pixels and 100 rows of 17.35.
    let grid = Grid::new(100, 100, 783, 1735, true).unwrap();
    let cell = |x, y| {
        let (cell, _) = cells(Some(grid), pixels(MouseEventKind::Moved, x, y));
        (cell.column, cell.row)
    };

    assert_eq!(cell(5, 872), (0, 50));
    assert_eq!(cell(5, 1566), (0, 90));
    assert_eq!(cell(700, 5), (89, 0));
}

#[test]
fn pixel_reports_past_the_text_area_clamp_to_the_last_cell() {
    let (cell, _) = cells(Some(GRID), pixels(MouseEventKind::Moved, 1005, 815));

    assert_eq!((cell.column, cell.row), (99, 39));
}

#[test]
fn edge_reports_wrapped_by_crossterm_map_to_the_first_cell() {
    let (cell, row) = cells(Some(GRID), pixels(MouseEventKind::Moved, 0, 0));

    assert_eq!((cell.column, cell.row), (0, 0));
    assert_eq!(row, 0.0);
}

#[test]
fn cell_reports_pass_through_without_pixel_mode() {
    let (cell, row) = cells(None, mouse(MouseEventKind::Moved, 7, 3));

    assert_eq!((cell.column, cell.row), (7, 3));
    assert_eq!(row, 3.0);
}

#[test]
fn the_probe_waits_for_the_device_attributes_reply() {
    assert!(!is_answered(b""));
    assert!(!is_answered(b"\x1b[?1016;1$y"));
    assert!(is_answered(b"\x1b[?1016;1$y\x1b[?62;22c"));
    assert!(is_answered(b"\x1b[?1;2c"));
}

#[test]
fn the_cell_size_reply_reads_as_width_and_height() {
    assert_eq!(
        cell_size(b"\x1b[?1016;2$y\x1b[6;36;17t\x1b[?62c"),
        Some((17, 36))
    );
    assert_eq!(cell_size(b"\x1b[?1016;2$y\x1b[?62c"), None);
    assert_eq!(cell_size(b"\x1b[6;0;17t"), None);
    assert_eq!(cell_size(b"\x1b[6;36t"), None);
}

#[test]
fn pixel_reports_need_window_cells_matching_the_probed_cell_size() {
    let leftover = Grid::new(100, 40, 1009, 819, false).unwrap();
    let fractional = Grid::new(100, 100, 1000, 1750, true).unwrap();
    let scaled = Grid {
        width: 2000,
        height: 1600,
        ..GRID
    };

    assert_eq!(validated(Some((10, 20)), Some(GRID)), Some(GRID));
    assert_eq!(validated(Some((10, 20)), Some(leftover)), Some(leftover));
    assert_eq!(
        validated(Some((10, 18)), Some(fractional)),
        Some(fractional)
    );
    assert_eq!(validated(Some((10, 20)), Some(scaled)), None);
    assert_eq!(validated(None, Some(GRID)), None);
    assert_eq!(validated(Some((10, 20)), None), None);
}

#[test]
fn leaving_pixel_reports_mid_session_restores_sgr_reports() {
    assert_eq!(transition(true, false), Some("\x1b[?1016l\x1b[?1006h"));
    assert_eq!(transition(false, true), Some("\x1b[?1016h"));
    assert_eq!(transition(true, true), None);
    assert_eq!(transition(false, false), None);
}

#[test]
fn capture_turned_on_after_a_cached_off_start_enables_pixel_reports() {
    let valid = validated(Some((10, 20)), Some(GRID));
    let off = reported_grid(valid, false, false);
    assert_eq!(
        off, None,
        "no pixel reports while the mouse is not captured"
    );
    assert_eq!(transition(false, off.is_some()), None);

    let on = reported_grid(valid, false, true);
    assert_eq!(on, Some(GRID));
    assert_eq!(transition(off.is_some(), on.is_some()), Some(ENABLE));
    assert_eq!(
        reported_grid(valid, true, true),
        None,
        "a gesture holds cells"
    );
    let unprobed = validated(None, Some(GRID));
    assert_eq!(reported_grid(unprobed, false, true), None, "unprobed");
}

#[test]
fn only_a_recognized_mode_1016_enables_pixel_reports() {
    assert!(supports(b"\x1b[?1016;1$y\x1b[?62;22c"));
    assert!(supports(b"\x1b[?1016;2$y\x1b[?62;22c"));
    assert!(!supports(b"\x1b[?1016;0$y\x1b[?62;22c"));
    assert!(!supports(b"\x1b[?1;2c"));
}

#[test]
fn motion_within_the_same_eighth_of_a_cell_is_dropped() {
    let mut coalesce = Coalesce::default();
    let drag = MouseEventKind::Drag(MouseButton::Left);

    assert!(coalesce.keep(Some(GRID), &Event::Mouse(pixels(drag, 505, 400))));
    assert!(!coalesce.keep(Some(GRID), &Event::Mouse(pixels(drag, 506, 401))));
    assert!(coalesce.keep(Some(GRID), &Event::Mouse(pixels(drag, 506, 403))));
    assert!(coalesce.keep(
        Some(GRID),
        &Event::Mouse(pixels(MouseEventKind::Moved, 506, 403))
    ));
}

#[test]
fn hover_motion_within_the_same_cell_is_dropped() {
    let mut coalesce = Coalesce::default();
    let moved = |x, y| Event::Mouse(pixels(MouseEventKind::Moved, x, y));

    assert!(coalesce.keep(Some(GRID), &moved(505, 400)));
    assert!(!coalesce.keep(Some(GRID), &moved(509, 419)));
    assert!(coalesce.keep(Some(GRID), &moved(509, 420)));
}

#[test]
fn other_events_reset_motion_coalescing() {
    let mut coalesce = Coalesce::default();
    let moved = Event::Mouse(pixels(MouseEventKind::Moved, 505, 400));

    assert!(coalesce.keep(Some(GRID), &moved));
    assert!(coalesce.keep(Some(GRID), &Event::FocusLost));
    assert!(coalesce.keep(Some(GRID), &moved));
}

#[test]
fn presses_and_other_events_are_never_dropped() {
    let mut coalesce = Coalesce::default();
    let press = Event::Mouse(down(5, 4));

    assert!(coalesce.keep(None, &press));
    assert!(coalesce.keep(None, &press));
    assert!(coalesce.keep(None, &Event::FocusGained));
    assert!(coalesce.keep(None, &Event::FocusGained));
}

#[test]
fn a_report_around_a_switch_to_cells_reads_in_the_units_nearer_the_pointer() {
    let drag = MouseEventKind::Drag(MouseButton::Left);
    let read = |event| {
        let ((cell, _), pixel) = settle(GRID, (50, 20), event, false);
        ((cell.column, cell.row), pixel)
    };

    assert_eq!(read(pixels(drag, 515, 430)), ((51, 21), true));
    assert_eq!(read(mouse(drag, 49, 19)), ((49, 19), false));
    assert_eq!(read(mouse(drag, 49, 0)), ((49, 0), false));
}

#[test]
fn a_tie_around_a_switch_reads_in_the_new_units() {
    let tie = mouse(MouseEventKind::Moved, 0, 0);

    assert!(settle(GRID, (0, 0), tie, true).1);
    assert!(!settle(GRID, (0, 0), tie, false).1);
}

#[test]
fn an_edge_pixel_report_around_a_switch_stays_a_pixel_report() {
    let ((cell, _), pixel) = settle(GRID, (0, 1), pixels(MouseEventKind::Moved, 0, 25), false);

    assert_eq!(((cell.column, cell.row), pixel), ((0, 1), true));
}

/// A 0-based SGR-pixels report as crossterm parses it: minus one, wrapping at 0.
fn pixels(kind: MouseEventKind, x: u16, y: u16) -> MouseEvent {
    mouse(kind, x.wrapping_sub(1), y.wrapping_sub(1))
}

#[cfg(unix)]
mod unix {
    use std::fs::File;
    use std::io::{Read, Write};
    use std::os::fd::FromRawFd;
    use std::thread;

    use vibe_rs::mouse::pixels::{probe_tty, QUERY};

    fn pty() -> (File, File) {
        let mut master = 0;
        let mut slave = 0;
        assert_eq!(
            unsafe {
                libc::openpty(
                    &mut master,
                    &mut slave,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                )
            },
            0
        );
        unsafe { (File::from_raw_fd(master), File::from_raw_fd(slave)) }
    }

    fn probe_with_reply(reply: &'static [u8]) -> Option<(u16, u16)> {
        let (master, mut slave) = pty();
        let mut responder = master.try_clone().unwrap();
        let thread = thread::spawn(move || {
            let mut query = [0; QUERY.len()];
            responder.read_exact(&mut query).unwrap();
            assert_eq!(query, QUERY);
            responder.write_all(reply).unwrap();
        });
        let supported = probe_tty(&mut slave);
        thread.join().unwrap();
        supported
    }

    #[test]
    fn a_terminal_recognizing_mode_1016_reports_its_cell_size() {
        assert_eq!(
            probe_with_reply(b"\x1b[?1016;2$y\x1b[6;36;17t\x1b[?62;22c"),
            Some((17, 36))
        );
    }

    #[test]
    fn a_terminal_without_a_cell_size_reply_keeps_cell_reports() {
        assert_eq!(probe_with_reply(b"\x1b[?1016;2$y\x1b[?62;22c"), None);
    }

    #[test]
    fn a_terminal_answering_only_device_attributes_keeps_cell_reports() {
        assert_eq!(probe_with_reply(b"\x1b[?62;22c"), None);
    }
}
