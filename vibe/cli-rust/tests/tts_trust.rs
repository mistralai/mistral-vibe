//! ADR 0015: the narrator's speech client builds under both trust policies.

use vibe_rs::tts::build_http_client;
use vibe_rs::update_notifier::gateway::configure_tls_trust;

#[test]
fn speech_client_builds_with_bundled_and_system_roots() {
    configure_tls_trust(true);
    assert!(build_http_client().is_ok());
    configure_tls_trust(false);
    assert!(build_http_client().is_ok());
}
