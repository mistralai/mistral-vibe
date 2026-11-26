//! Seeded frame delays (`VIBE_REPLAY_JITTER=<seed>`): goldens must not change under them.

use std::time::Duration;

const ENV: &str = "VIBE_REPLAY_JITTER";
const MAX_DELAY_MS: u64 = 20;

/// A splitmix64 stream; the same seed replays the same delays.
pub struct Jitter(u64);

impl Jitter {
    /// The jitter the env asks for, or `None` when it is unset or not a number.
    pub fn from_env() -> Option<Self> {
        std::env::var(ENV).ok()?.parse().ok().map(Self)
    }

    /// Sleep 0..=20 ms before the next frame.
    pub fn delay(&mut self) {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^= z >> 31;
        std::thread::sleep(Duration::from_millis(z % (MAX_DELAY_MS + 1)));
    }
}
