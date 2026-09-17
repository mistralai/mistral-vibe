//! Shared xorshift64 PRNG for the braille animations.

use std::time::{SystemTime, UNIX_EPOCH};

/// xorshift64 seeded from the wall clock.
pub struct Rng {
    state: u64,
}

impl Default for Rng {
    fn default() -> Self {
        let seed = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0x9E37_79B9_7F4A_7C15)
            | 1;
        Self { state: seed }
    }
}

impl Rng {
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.state = x;
        x
    }

    /// Uniform f64 in `[0, 1)`.
    pub fn random(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }

    /// Uniform f64 in `[a, b)`.
    pub fn uniform(&mut self, a: f64, b: f64) -> f64 {
        a + self.random() * (b - a)
    }
}
