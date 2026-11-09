//! Windows theme probes.

use std::io::Write;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use super::{classify_osc11_response, is_complete_osc11_response, OSC11_QUERY};

const OSC11_TIMEOUT: Duration = Duration::from_millis(500);
const MAX_RESPONSE_BYTES: usize = 256;
const OS_APPEARANCE_TIMEOUT: Duration = Duration::from_secs(1);

#[link(name = "msvcrt")]
extern "C" {
    fn _kbhit() -> i32;
    fn _getch() -> i32;
}

pub(super) fn detect_terminal_dark() -> Option<bool> {
    if unsafe { _kbhit() } != 0 {
        return None;
    }
    let mut terminal = std::io::stderr();
    terminal.write_all(OSC11_QUERY).ok()?;
    terminal.flush().ok()?;
    let deadline = Instant::now() + OSC11_TIMEOUT;
    let mut response = Vec::new();
    while Instant::now() < deadline
        && response.len() < MAX_RESPONSE_BYTES
        && !is_complete_osc11_response(&response)
    {
        while unsafe { _kbhit() } != 0 && response.len() < MAX_RESPONSE_BYTES {
            let byte = unsafe { _getch() };
            if byte < 0 {
                return None;
            }
            response.push(byte as u8);
            if is_complete_osc11_response(&response) {
                break;
            }
        }
        if !is_complete_osc11_response(&response) {
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    classify_osc11_response(&response)
}

pub(super) fn detect_system_preferred_dark() -> Option<bool> {
    let (success, stdout) = command_output(
        "reg",
        &[
            "query",
            r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            "/v",
            "AppsUseLightTheme",
        ],
    )?;
    super::classify_windows_appearance(success, &stdout)
}

fn command_output(program: &str, args: &[&str]) -> Option<(bool, String)> {
    let mut child = Command::new(program)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let deadline = Instant::now() + OS_APPEARANCE_TIMEOUT;
    let status = loop {
        if let Some(status) = child.try_wait().ok()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let stdout = child.stdout.take()?;
    let mut text = String::new();
    std::io::Read::read_to_string(&mut std::io::BufReader::new(stdout), &mut text).ok()?;
    Some((status.success(), text))
}
