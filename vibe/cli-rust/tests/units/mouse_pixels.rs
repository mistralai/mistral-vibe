//! SGR-pixels mouse reports: support probe, pixel-to-cell conversion, motion coalescing.

use crossterm::event::{Event, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use vibe_rs::mouse::pixels::{
    cell_size, cells, is_answered, supports, transition, validated, Coalesce, Grid,
};

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
    let grid = Grid {
        width: 1009,
        height: 819,
        ..GRID
    };

    let (cell, row) = cells(Some(grid), pixels(MouseEventKind::Moved, 995, 795));

    assert_eq!((cell.column, cell.row), (99, 39));
    assert_eq!(row, 39.75);
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
    let (cell, row) = cells(None, event(MouseEventKind::Moved, 7, 3));

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
    let leftover = Grid {
        width: 1009,
        height: 819,
        ..GRID
    };
    let scaled = Grid {
        width: 2000,
        height: 1600,
        ..GRID
    };

    assert_eq!(validated(Some((10, 20)), Some(GRID)), Some(GRID));
    assert_eq!(validated(Some((10, 20)), Some(leftover)), Some(leftover));
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
    let press = Event::Mouse(event(MouseEventKind::Down(MouseButton::Left), 5, 4));

    assert!(coalesce.keep(None, &press));
    assert!(coalesce.keep(None, &press));
    assert!(coalesce.keep(None, &Event::FocusGained));
    assert!(coalesce.keep(None, &Event::FocusGained));
}

/// A 0-based SGR-pixels report as crossterm parses it: minus one, wrapping at 0.
fn pixels(kind: MouseEventKind, x: u16, y: u16) -> MouseEvent {
    event(kind, x.wrapping_sub(1), y.wrapping_sub(1))
}

fn event(kind: MouseEventKind, column: u16, row: u16) -> MouseEvent {
    MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    }
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
