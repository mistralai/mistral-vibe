//! The startup terminal query: the palette, mouse pixel-report support, and XTVERSION, in one round trip that DA1 bounds.

/// Device Attributes (DA1), which every terminal answers, so its reply ends the read.
const DA1: &[u8] = b"\x1b[c";

/// Ask the terminal once, then hand its reply to the palette and to mouse pixel reports; it always runs since DA1 bounds it and a later theme switch may need the palette.
pub fn run() {
    #[cfg(unix)]
    {
        if crate::tty_query::in_multiplexer() {
            return;
        }
        let response = crate::tty_query::open().and_then(|mut tty| query(&mut tty));
        crate::terminal_capabilities::record(response.as_deref());
        let Some(response) = response else {
            return;
        };
        crate::terminal_colors::record(&response);
        crate::mouse::pixels::record(&response);
    }
}

/// The terminal's reply to the palette and pixel-report queries, read until DA1.
#[cfg(unix)]
pub fn query(tty: &mut std::fs::File) -> Option<Vec<u8>> {
    crate::tty_query::query(tty, &request(), is_answered)
}

/// The bytes the probe sends.
pub fn request() -> Vec<u8> {
    [
        crate::terminal_colors::QUERY,
        crate::mouse::pixels::QUERY,
        crate::terminal_capabilities::QUERY,
        DA1,
    ]
    .concat()
}

/// The DA1 reply (`CSI ? … c`) arrived: the replies before it came first.
pub fn is_answered(response: &[u8]) -> bool {
    response
        .rsplit(|byte| *byte == 0x1b)
        .next()
        .is_some_and(|last| last.starts_with(b"[?") && last.ends_with(b"c"))
}
