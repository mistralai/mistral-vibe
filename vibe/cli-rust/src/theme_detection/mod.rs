//! Terminal and operating-system theme detection.

use std::sync::OnceLock;

#[cfg(unix)]
mod unix;
#[cfg(windows)]
mod windows;

#[cfg(unix)]
use unix as platform;
#[cfg(windows)]
use windows as platform;

#[cfg(not(any(unix, windows)))]
mod platform {
    pub(super) fn detect_terminal_dark() -> Option<bool> {
        None
    }

    pub(super) fn detect_system_preferred_dark() -> Option<bool> {
        None
    }
}

#[cfg(unix)]
pub use unix::detect_terminal_dark_from;

pub const OSC11_QUERY: &[u8] = b"\x1b]11;?\x07";
const OSC11_RESPONSE_PREFIX: &[u8] = b"\x1b]11;rgb:";
const LUMINANCE_DARK_THRESHOLD: f64 = 0.5;
const SRGB_LINEARIZE_CUTOFF: f64 = 0.03928;
const DARK_THEME: &str = "ansi-dark";
const LIGHT_THEME: &str = "ansi-light";
static AUTO_THEME: OnceLock<&'static str> = OnceLock::new();

pub fn resolve_auto_theme() -> &'static str {
    AUTO_THEME.get_or_init(|| {
        theme_from_preferences(
            platform::detect_terminal_dark().or_else(platform::detect_system_preferred_dark),
        )
    })
}

pub fn theme_from_preferences(dark: Option<bool>) -> &'static str {
    match dark {
        Some(false) => LIGHT_THEME,
        Some(true) | None => DARK_THEME,
    }
}

pub fn classify_macos_appearance(success: bool, output: &str) -> bool {
    success && output.contains("Dark")
}

pub fn classify_linux_appearance(success: bool, output: &str) -> Option<bool> {
    if !success {
        return None;
    }
    match output.trim().trim_matches('\'') {
        "prefer-dark" => Some(true),
        "prefer-light" => Some(false),
        _ => None,
    }
}

pub fn classify_windows_appearance(success: bool, output: &str) -> Option<bool> {
    if !success {
        return None;
    }
    let raw = output
        .lines()
        .find(|line| line.contains("AppsUseLightTheme"))?
        .split_whitespace()
        .next_back()?;
    let value = u32::from_str_radix(raw.trim_start_matches("0x"), 16).ok()?;
    Some(value == 0)
}

pub fn classify_osc11_response(response: &[u8]) -> Option<bool> {
    let start = response
        .windows(OSC11_RESPONSE_PREFIX.len())
        .position(|window| window == OSC11_RESPONSE_PREFIX)?;
    let mut payload = &response[start + OSC11_RESPONSE_PREFIX.len()..];
    let (red, consumed) = parse_hex_component(payload)?;
    payload = payload
        .get(consumed + 1..)
        .filter(|_| payload.get(consumed) == Some(&b'/'))?;
    let (green, consumed) = parse_hex_component(payload)?;
    payload = payload
        .get(consumed + 1..)
        .filter(|_| payload.get(consumed) == Some(&b'/'))?;
    let (blue, _) = parse_hex_component(payload)?;
    Some(relative_luminance(red, green, blue) < LUMINANCE_DARK_THRESHOLD)
}

pub(super) fn is_complete_osc11_response(response: &[u8]) -> bool {
    let Some(start) = response
        .windows(OSC11_RESPONSE_PREFIX.len())
        .position(|window| window == OSC11_RESPONSE_PREFIX)
    else {
        return false;
    };
    let payload = &response[start + OSC11_RESPONSE_PREFIX.len()..];
    payload.contains(&b'\x07') || payload.windows(2).any(|window| window == b"\x1b\\")
}

fn parse_hex_component(input: &[u8]) -> Option<(f64, usize)> {
    let length = input
        .iter()
        .take_while(|byte| byte.is_ascii_hexdigit())
        .count();
    if length == 0 {
        return None;
    }
    let mut value = 0.0;
    let mut maximum = 0.0;
    for byte in &input[..length] {
        value = value * 16.0 + char::from(*byte).to_digit(16)? as f64;
        maximum = maximum * 16.0 + 15.0;
    }
    Some((value / maximum, length))
}

fn relative_luminance(red: f64, green: f64, blue: f64) -> f64 {
    fn linearize(component: f64) -> f64 {
        if component <= SRGB_LINEARIZE_CUTOFF {
            component / 12.92
        } else {
            ((component + 0.055) / 1.055).powf(2.4)
        }
    }

    0.2126 * linearize(red) + 0.7152 * linearize(green) + 0.0722 * linearize(blue)
}
