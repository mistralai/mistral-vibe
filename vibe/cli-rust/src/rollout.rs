//! Fallback hint for launches picked by the GrowthBook Rust TUI rollout.

/// Set to `1` by the Python `vibe` launcher when the rollout picked this binary.
pub const ROLLOUT_ENV: &str = "VIBE_RUST_ROLLOUT";

pub const FALLBACK_HINT: &str = "You are using the new Vibe TUI, currently in preview. \
Please report this error at https://github.com/mistralai/mistral-vibe/issues. \
To switch back to the classic TUI, set VIBE_CLI=python.";

pub fn is_rollout_launch() -> bool {
    std::env::var_os(ROLLOUT_ENV).is_some_and(|value| value == "1")
}

pub fn print_fallback_hint() {
    if is_rollout_launch() {
        eprintln!("{FALLBACK_HINT}");
    }
}

/// Install before the other panic hooks so the hint prints after the panic message.
pub fn install_panic_hint() {
    if !is_rollout_launch() {
        return;
    }
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        previous(info);
        eprintln!("{FALLBACK_HINT}");
    }));
}
