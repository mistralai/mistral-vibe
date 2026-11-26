//! The startup terminal probe: one query for the palette and pixel-report support, read until DA1.

use vibe_rs::terminal_capabilities::{from_response, read, xtversion, Capabilities};
use vibe_rs::terminal_probe::is_answered;

#[test]
fn the_probe_waits_for_the_device_attributes_reply() {
    assert!(!is_answered(b""));
    assert!(!is_answered(b"\x1b[?1016;1$y"));
    assert!(is_answered(b"\x1b[?1016;1$y\x1b[?62;22c"));
    assert!(is_answered(b"\x1b[?1;2c"));
}

#[test]
fn xtversion_replies_are_read() {
    assert_eq!(
        xtversion(b"\x1bP>|ghostty 1.1.3\x1b\\\x1b[?62c"),
        Some("ghostty 1.1.3".into())
    );
    assert_eq!(xtversion(b"\x1b[?62c"), None);
}

#[test]
fn the_probe_status_tells_a_silent_terminal_from_an_unqueried_one() {
    assert_eq!(read(b"\x1b[?62;22c").status, "answered");
    assert_eq!(read(b"\x1b[?1016;2$y").status, "unanswered");
    assert_eq!(from_response(None).status, "not_queried");
    assert_eq!(Capabilities::default().status, "skipped");
}

#[cfg(unix)]
mod unix {
    use std::fs::File;
    use std::io::{Read, Write};
    use std::os::fd::FromRawFd;
    use std::thread;
    use std::time::{Duration, Instant};

    use vibe_rs::mouse::pixels::reported_cell;
    use vibe_rs::terminal_capabilities::read;
    use vibe_rs::terminal_colors;
    use vibe_rs::terminal_probe::{query, request};

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

    fn probe_with_reply(reply: &'static [u8]) -> Vec<u8> {
        let (master, mut slave) = pty();
        let mut responder = master.try_clone().unwrap();
        let thread = thread::spawn(move || {
            let mut sent = vec![0; request().len()];
            responder.read_exact(&mut sent).unwrap();
            assert_eq!(sent, request());
            responder.write_all(reply).unwrap();
        });
        let response = query(&mut slave).unwrap();
        thread.join().unwrap();
        response
    }

    #[test]
    fn a_terminal_recognizing_mode_1016_reports_its_cell_size() {
        let response = probe_with_reply(b"\x1b[?1016;2$y\x1b[6;36;17t\x1b[?62;22c");
        assert_eq!(reported_cell(&response), Some((17, 36)));
    }

    #[test]
    fn a_terminal_without_a_cell_size_reply_keeps_cell_reports() {
        let response = probe_with_reply(b"\x1b[?1016;2$y\x1b[?62;22c");
        assert_eq!(reported_cell(&response), None);
    }

    #[test]
    fn a_terminal_answering_only_device_attributes_keeps_cell_reports() {
        let response = probe_with_reply(b"\x1b[?62;22c");
        assert_eq!(reported_cell(&response), None);
        assert!(terminal_colors::read(&response).iter().all(Option::is_none));
    }

    #[test]
    fn palette_replies_come_back_with_the_pixel_replies() {
        let response = probe_with_reply(
            b"\x1b]4;4;rgb:0000/0000/eeee\x1b\\\x1b]4;6;rgb:0000/cdcd/cdcd\x1b\\\x1b]4;12;rgb:5c5c/5c5c/ffff\x1b\\\x1b[?1016;2$y\x1b[6;36;17t\x1b[?62;22c",
        );
        let palette = terminal_colors::read(&response);
        assert_eq!(palette[4], Some([0x00, 0x00, 0xee]));
        assert_eq!(palette[6], Some([0x00, 0xcd, 0xcd]));
        assert_eq!(palette[12], Some([0x5c, 0x5c, 0xff]));
        assert_eq!(reported_cell(&response), Some((17, 36)));
    }

    #[test]
    fn a_terminal_that_never_sends_device_attributes_ends_the_probe_at_the_timeout() {
        let (master, mut slave) = pty();
        let mut responder = master.try_clone().unwrap();
        let thread = thread::spawn(move || {
            let mut sent = vec![0; request().len()];
            responder.read_exact(&mut sent).unwrap();
            responder.write_all(b"\x1b[?1016;2$y").unwrap();
        });
        let started = Instant::now();
        let response = query(&mut slave).unwrap();
        let waited = started.elapsed();
        thread.join().unwrap();
        assert_eq!(response, b"\x1b[?1016;2$y");
        assert_eq!(read(&response).status, "unanswered");
        assert!(
            waited >= Duration::from_millis(400),
            "returned after {waited:?}"
        );
        assert!(waited < Duration::from_secs(5), "returned after {waited:?}");
    }

    #[test]
    fn xtversion_and_pixel_replies_come_back_together() {
        let response =
            probe_with_reply(b"\x1b[?1016;2$y\x1b[6;36;17t\x1bP>|kitty(0.35.2)\x1b\\\x1b[?62;22c");
        let capabilities = read(&response);
        assert_eq!(capabilities.status, "answered");
        assert_eq!(capabilities.version.as_deref(), Some("kitty(0.35.2)"));
        assert_eq!(capabilities.mouse_pixel_reports, Some(true));
    }
}
