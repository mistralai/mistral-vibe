//! The wizard loop guard (the blocking missing-key fix): a repeated
//! consecutive missing-key verdict for the same provider fails the run
//! instead of reopening the wizard forever — an `--agent other` run with
//! an unset `OPENROUTER_API_KEY` loops without it. The verdict's provider
//! also names the wizard's `setup/status` seed; that wire half is pinned
//! in `onboarding_fake_server.rs`.

use vibe_rs::setup::rounds::OnboardingGuard;

#[test]
fn a_repeated_verdict_for_the_same_provider_trips_the_guard() {
    let mut guard = OnboardingGuard::default();
    assert!(
        !guard.repeats("openrouter"),
        "the first verdict opens the wizard"
    );
    assert!(
        guard.repeats("openrouter"),
        "the second consecutive verdict for the same provider exits 1"
    );
}

#[test]
fn a_different_provider_opens_the_wizard_again() {
    let mut guard = OnboardingGuard::default();
    assert!(!guard.repeats("mistral"));
    assert!(
        !guard.repeats("openrouter"),
        "a different provider means a new wizard round"
    );
    assert!(guard.repeats("openrouter"));
}
