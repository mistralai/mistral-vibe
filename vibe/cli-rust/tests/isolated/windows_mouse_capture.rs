//! Windows mouse capture requests SGR reports, then restores the console mode and encoding.
#![cfg(windows)]

use std::fs::{File, OpenOptions};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::process::Command;

use vibe_rs::terminal::{capture_mouse, release_mouse};
use windows_sys::Win32::System::Console::{GetConsoleMode, SetConsoleMode, ENABLE_MOUSE_INPUT};
use windows_sys::Win32::System::Threading::CREATE_NEW_CONSOLE;

fn console_mode(input: &File) -> u32 {
    let mut mode = 0;
    // SAFETY: `input` is an open console handle and `mode` a valid out-parameter.
    let ok = unsafe { GetConsoleMode(input.as_raw_handle(), &mut mode) };
    assert_ne!(ok, 0, "{}", std::io::Error::last_os_error());
    mode
}

fn contains(output: &[u8], sequence: &[u8]) -> bool {
    output
        .windows(sequence.len())
        .any(|window| window == sequence)
}

#[test]
fn mouse_capture_requests_sgr_reports_and_restores_the_console() {
    let output = Command::new(std::env::current_exe().expect("test executable"))
        .args([
            "--ignored",
            "--exact",
            &test_name!("helper_capture_in_new_console"),
            "--nocapture",
        ])
        .creation_flags(CREATE_NEW_CONSOLE)
        .output()
        .expect("run helper in its own console");
    assert!(
        output.status.success() && String::from_utf8_lossy(&output.stdout).contains("1 passed"),
        "stdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
}

#[test]
#[ignore]
fn helper_capture_in_new_console() {
    let input = OpenOptions::new()
        .read(true)
        .write(true)
        .open("CONIN$")
        .expect("open console input");
    let original = console_mode(&input) & !ENABLE_MOUSE_INPUT;
    // SAFETY: `input` is an open console handle and `original` a valid input mode.
    let ok = unsafe { SetConsoleMode(input.as_raw_handle(), original) };
    assert_ne!(ok, 0, "{}", std::io::Error::last_os_error());

    let mut output = Vec::new();
    capture_mouse(&mut output).expect("capture mouse");
    assert_ne!(console_mode(&input) & ENABLE_MOUSE_INPUT, 0);
    assert!(contains(&output, b"\x1b[?1006h"), "{output:?}");

    output.clear();
    release_mouse(&mut output).expect("release mouse");
    assert_eq!(console_mode(&input), original);
    assert!(contains(&output, b"\x1b[?1006l"), "{output:?}");
}
