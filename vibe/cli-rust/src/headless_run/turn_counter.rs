//! Client-side `--max-turns` enforcement.

use serde_json::Value;

/// Counts the session's assistant turns and flags when one more would exceed
/// `--max-turns`. The stdio app-server ignores the session's `maxTurns`, so the
/// client enforces it the way the in-process harness would: the turn stops as
/// a limit once the assistant entries outnumber it.
pub struct TurnCounter {
    limit: Option<u32>,
    seen: u32,
}

impl TurnCounter {
    pub fn new(limit: Option<u32>) -> Self {
        Self { limit, seen: 0 }
    }

    /// Record one added history entry. True when the turn limit is exceeded
    /// — on the (limit+1)th assistant entry, one more turn than the limit
    /// allows — which is when the run stops, like the harness's
    /// `TurnLimitMiddleware` capping its iterations.
    pub fn observe(&mut self, entry: &Value) -> bool {
        if entry.get("role").and_then(Value::as_str) != Some("assistant") {
            return false;
        }
        self.seen += 1;
        self.limit.is_some_and(|limit| self.seen > limit)
    }
}
