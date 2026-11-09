//! Wire `AgentStatsSnapshot` (app-server `models.py`); every field defaults (ADR 0014).

use serde::Deserialize;

#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct SessionStats {
    pub steps: u64,
    pub model_calls: Option<u64>,
    pub context_tokens: u64,
    pub session_prompt_tokens: u64,
    pub session_completion_tokens: u64,
    pub session_cached_tokens: u64,
    pub last_turn_prompt_tokens: u64,
    pub last_turn_completion_tokens: u64,
    pub last_turn_cached_tokens: u64,
    pub input_price_per_million: f64,
    pub output_price_per_million: f64,
    pub cached_input_price_per_million: Option<f64>,
}

impl SessionStats {
    pub fn total_tokens(&self) -> u64 {
        self.session_prompt_tokens + self.session_completion_tokens
    }

    /// The app server's `session_token_cost`, in dollars.
    pub fn cost(&self) -> f64 {
        let input_price = self.input_price_per_million;
        let cached_price = self.cached_input_price_per_million.unwrap_or(input_price);
        let cached = self.session_cached_tokens.min(self.session_prompt_tokens);
        let input = (self.session_prompt_tokens - cached) as f64 * input_price;
        let output = self.session_completion_tokens as f64 * self.output_price_per_million;
        (input + cached as f64 * cached_price + output) / 1_000_000.0
    }
}
