//! Auto-theme resolution and OSC 11 parsing.

use vibe_rs::theme_detection::{
    classify_linux_appearance, classify_macos_appearance, classify_osc11_response,
    classify_windows_appearance, theme_from_preferences,
};

#[test]
fn classifies_dark_and_light_backgrounds() {
    assert_eq!(
        classify_osc11_response(b"\x1b]11;rgb:0000/0000/0000\x07"),
        Some(true)
    );
    assert_eq!(
        classify_osc11_response(b"\x1b]11;rgb:ffff/ffff/ffff\x07"),
        Some(false)
    );
}

#[test]
fn accepts_short_components_and_string_terminators() {
    assert_eq!(
        classify_osc11_response(b"noise\x1b]11;rgb:00/00/00\x1b\\"),
        Some(true)
    );
    assert_eq!(
        classify_osc11_response(b"\x1b]11;rgb:ff/ff/ff\x1b\\"),
        Some(false)
    );
}

#[test]
fn rejects_incomplete_or_unrelated_responses() {
    assert_eq!(classify_osc11_response(b""), None);
    assert_eq!(classify_osc11_response(b"\x1b]10;rgb:00/00/00\x07"), None);
    assert_eq!(classify_osc11_response(b"\x1b]11;rgb:00/00"), None);
}

#[test]
fn resolves_a_preference_or_falls_back_to_dark() {
    assert_eq!(theme_from_preferences(Some(false)), "ansi-light");
    assert_eq!(theme_from_preferences(Some(true)), "ansi-dark");
    assert_eq!(theme_from_preferences(None), "ansi-dark");
}

#[test]
fn classifies_each_supported_system_preference() {
    assert!(classify_macos_appearance(true, "Dark\n"));
    assert!(!classify_macos_appearance(false, ""));
    assert_eq!(
        classify_linux_appearance(true, "'prefer-dark'\n"),
        Some(true)
    );
    assert_eq!(
        classify_linux_appearance(true, "'prefer-light'\n"),
        Some(false)
    );
    assert_eq!(classify_linux_appearance(true, "'default'\n"), None);
    assert_eq!(
        classify_windows_appearance(true, "    AppsUseLightTheme    REG_DWORD    0x0\r\n"),
        Some(true)
    );
    assert_eq!(
        classify_windows_appearance(true, "    AppsUseLightTheme    REG_DWORD    0x1\r\n"),
        Some(false)
    );
}

#[cfg(unix)]
mod unix {
    use std::fs::File;
    use std::io::{Read, Write};
    use std::os::fd::FromRawFd;
    use std::thread;

    use vibe_rs::theme_detection::{detect_terminal_dark_from, OSC11_QUERY};

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

    #[test]
    fn queries_the_tty_and_preserves_bytes_after_the_response() {
        let (master, mut slave) = pty();
        let mut responder = master.try_clone().unwrap();
        let thread = thread::spawn(move || {
            let mut query = [0; OSC11_QUERY.len()];
            responder.read_exact(&mut query).unwrap();
            assert_eq!(query, OSC11_QUERY);
            responder
                .write_all(b"\x1b]11;rgb:ffff/ffff/ffff\x07typed while probing\n")
                .unwrap();
        });

        assert_eq!(
            detect_terminal_dark_from(slave.try_clone().unwrap()),
            Some(false)
        );
        let mut trailing = [0; 20];
        slave.read_exact(&mut trailing).unwrap();
        assert_eq!(&trailing, b"typed while probing\n");
        thread.join().unwrap();
    }

    #[test]
    fn does_not_probe_when_input_is_already_pending() {
        let (mut master, mut slave) = pty();
        master.write_all(b"pasted command\n").unwrap();
        let mut echo = [0; 16];
        master.read_exact(&mut echo).unwrap();
        assert_eq!(&echo, b"pasted command\r\n");

        assert_eq!(detect_terminal_dark_from(slave.try_clone().unwrap()), None);
        let mut pending = [0; 15];
        slave.read_exact(&mut pending).unwrap();
        assert_eq!(&pending, b"pasted command\n");
    }
}
