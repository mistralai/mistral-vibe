//! The setup wire-shape pin: the fixture holds the canonical payloads of
//! the Python protocol models (`model_dump(by_alias=True)` — the camelCase
//! the app-server serializes with and validates against), generated from
//! `vibe/app_server/protocol.py`'s real models. Each Rust struct must
//! round-trip it byte-for-byte, so the client can never re-diverge from
//! the server (a fresh install's wizard reads exactly these frames). The
//! status response the client keeps is the fixture minus `activeModel`
//! (still sent for other surfaces; the wizard never reads it), and the
//! outcome tags deserialize through the typed enums.

use serde_json::Value;

use super::onboarding_seed::status;
use vibe_rs::setup::auth::rpc::{
    ProviderView, SetupStatus, StoreCredential, StoreOutcome, SubmitChoices, SubmitOutcome,
};

fn fixture() -> Value {
    serde_json::from_str(include_str!("../onboarding_common/setup_wire.json")).expect("fixture")
}

fn provider_view() -> ProviderView {
    status::default_status().provider
}

#[test]
fn the_status_response_round_trips_the_python_payload() {
    let mut expected = fixture()["statusResponse"].clone();
    let status: SetupStatus =
        serde_json::from_value(expected.clone()).expect("deserializes the server's frame");
    assert_eq!(
        status.provider,
        provider_view(),
        "the camelCase provider frame feeds the snake_case fields"
    );
    // The server still sends `activeModel` (other surfaces read it); the
    // wizard never does, so the client's frame is the payload without it.
    expected
        .as_object_mut()
        .expect("status object")
        .remove("activeModel");
    assert_eq!(
        serde_json::to_value(&status).expect("serializes"),
        expected,
        "the wire shape matches the Python model_dump(by_alias=True)"
    );
}

#[test]
fn store_credential_params_match_the_python_payload() {
    let params = serde_json::to_value(StoreCredential {
        provider: "mistral",
        api_key: "sk-mock-key",
        custom_domain: true,
    })
    .expect("serializes");
    assert_eq!(params, fixture()["storeCredentialParams"]);
}

#[test]
fn submit_choices_params_match_the_python_payload() {
    let params = serde_json::to_value(SubmitChoices {
        provider: Some(provider_view()),
        console_base_url: Some("https://console.mistral.ai".into()),
        vibe_base_url: Some("https://chat.mistral.ai".into()),
        theme: Some("auto".into()),
    })
    .expect("serializes");
    assert_eq!(params, fixture()["submitChoicesParams"]);
}

#[test]
fn the_store_outcomes_round_trip_the_python_payload() {
    let outcomes = fixture()["storeResponses"].clone();
    let parsed: Vec<StoreOutcome> = outcomes
        .as_array()
        .expect("store outcomes")
        .iter()
        .map(|frame| serde_json::from_value(frame.clone()).expect("store outcome frame"))
        .collect();
    assert_eq!(
        parsed,
        vec![
            StoreOutcome::Completed,
            StoreOutcome::EnvVarError {
                detail: String::new()
            },
            StoreOutcome::SaveError {
                detail: "no space left".into()
            },
        ],
        "the snake_case outcome tag plus detail is the whole contract"
    );
}

#[test]
fn the_submit_outcomes_round_trip_the_python_payload() {
    let outcomes = fixture()["submitResponses"].clone();
    let parsed: Vec<SubmitOutcome> = outcomes
        .as_array()
        .expect("submit outcomes")
        .iter()
        .map(|frame| serde_json::from_value(frame.clone()).expect("submit outcome frame"))
        .collect();
    assert_eq!(
        parsed,
        vec![
            SubmitOutcome::Completed,
            SubmitOutcome::ProviderConfigError {
                failures: vec!["provider".into(), "console_base_url".into()],
            },
        ],
        "the snake_case outcome tag plus failures is the whole contract"
    );
}
