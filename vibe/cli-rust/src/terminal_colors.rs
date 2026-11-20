//! The terminal's own colors, read from OSC replies: the palette probed at startup and the color reply parser.

use std::sync::OnceLock;

/// Palette colors 0, 4, 6, 12 and 15 (OSC 4); `ui::theme::palette` resolves the named colors themes use.
pub const QUERY: &[u8] =
    b"\x1b]4;0;?\x1b\\\x1b]4;4;?\x1b\\\x1b]4;6;?\x1b\\\x1b]4;12;?\x1b\\\x1b]4;15;?\x1b\\";

/// The 16 named palette colors, `None` where the terminal did not report one.
pub type Palette = [Option<[u8; 3]>; 16];

static PALETTE: OnceLock<Palette> = OnceLock::new();

/// Keep the palette a startup probe response reports.
pub fn record(response: &[u8]) {
    let _ = PALETTE.set(read(response));
}

/// The palette colors (`ESC ] 4 ; n ; rgb:…`) in a probe response.
pub fn read(response: &[u8]) -> Palette {
    std::array::from_fn(|index| rgb_reply(response, format!("\x1b]4;{index};rgb:").as_bytes()))
}

/// Palette color `index` as the startup probe recorded it.
pub fn palette(index: usize) -> Option<[u8; 3]> {
    PALETTE.get()?.get(index).copied().flatten()
}

/// The color after `prefix` in `response`: one to four hex digits per channel, ended by BEL or ST.
pub fn rgb_reply(response: &[u8], prefix: &[u8]) -> Option<[u8; 3]> {
    let start = response
        .windows(prefix.len())
        .position(|window| window == prefix)?
        + prefix.len();
    let rest = &response[start..];
    let end = rest.iter().position(|&byte| byte == 0x07 || byte == 0x1b)?;
    let text = std::str::from_utf8(&rest[..end]).ok()?;
    let mut channels = text.split('/').map(|hex| {
        let digits = u32::try_from(hex.len())
            .ok()
            .filter(|len| (1..=4).contains(len))?;
        let value = u32::from_str_radix(hex, 16).ok()?;
        let maximum = 16u32.pow(digits) - 1;
        u8::try_from((value * 255 + maximum / 2) / maximum).ok()
    });
    let rgb = [channels.next()??, channels.next()??, channels.next()??];
    channels.next().is_none().then_some(rgb)
}
