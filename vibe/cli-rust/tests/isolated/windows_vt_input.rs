//! Windows VT input stays off while the TUI owns the console, then comes back as it was.
#![cfg(windows)]

use std::fs::{File, OpenOptions};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::process::Command;

use vibe_rs::platform::{disable_vt_input, keep_key_records, restore_vt_input};
use windows_sys::Win32::System::Console::{
    GetConsoleMode, SetConsoleMode, ENABLE_VIRTUAL_TERMINAL_INPUT,
};
use windows_sys::Win32::System::Threading::CREATE_NEW_CONSOLE;

fn console_mode(input: &File) -> u32 {
    let mut mode = 0;
    // SAFETY: `input` is an open console handle and `mode` a valid out-parameter.
    let ok = unsafe { GetConsoleMode(input.as_raw_handle(), &mut mode) };
    assert_ne!(ok, 0, "{}", std::io::Error::last_os_error());
    mode
}

fn turn_vt_input_on(input: &File) {
    let mode = console_mode(input) | ENABLE_VIRTUAL_TERMINAL_INPUT;
    // SAFETY: `input` is an open console handle and `mode` a valid input mode.
    let ok = unsafe { SetConsoleMode(input.as_raw_handle(), mode) };
    assert_ne!(ok, 0, "{}", std::io::Error::last_os_error());
}

fn vt_input(input: &File) -> bool {
    console_mode(input) & ENABLE_VIRTUAL_TERMINAL_INPUT != 0
}

#[test]
fn vt_input_stays_off_while_owned_and_is_restored() {
    let output = Command::new(std::env::current_exe().expect("test executable"))
        .args([
            "--ignored",
            "--exact",
            &test_name!("helper_vt_input_in_new_console"),
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
fn helper_vt_input_in_new_console() {
    let input = OpenOptions::new()
        .read(true)
        .write(true)
        .open("CONIN$")
        .expect("open console input");
    turn_vt_input_on(&input);

    let was_on = disable_vt_input();
    assert!(was_on);
    assert!(!vt_input(&input));

    turn_vt_input_on(&input);
    keep_key_records();
    assert!(
        !vt_input(&input),
        "another process turned VT input on mid-session"
    );

    restore_vt_input(was_on);
    assert!(vt_input(&input));
    keep_key_records();
    assert!(vt_input(&input), "released consoles keep their mode");
}
