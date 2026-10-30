//! First-party audio credential swaps through a fake app-server.

use serde_json::json;
use vibe_rs::credentials::{oidc, resolve_api_key};
use vibe_rs::setup::wizard::{actions, Action, OnboardingState};

use crate::onboarding_common::{fake_server, sandbox};

#[test]
#[ignore]
fn headers_server() {
    fake_server::serve_scripted(|method, msg| {
        Ok(match method {
            "setup/oidc-headers" => {
                assert!(msg["params"]["url"].as_str().is_some());
                json!({"headers": {"Authorization": "Bearer id-token", "x-external-organization-id": "org"}})
            }
            _ => json!({}),
        })
    });
}

#[test]
#[ignore]
fn rejected_server() {
    fake_server::serve_scripted(|_, _| {
        Err(json!({"code": -32602, "message": "re-login required"}))
    });
}

#[tokio::test]
async fn rust_audio_uses_server_oidc_headers_instead_of_saved_key() {
    let _sandbox = sandbox::sandbox();
    std::env::set_var("VIBE_CLI_OIDC_ENABLED", "true");
    std::env::set_var("MISTRAL_API_KEY", "ignored-api-key");
    assert_eq!(
        resolve_api_key("MISTRAL_API_KEY").as_deref(),
        Some(oidc::API_KEY_MARKER)
    );
    let (client, _child) = fake_server::spawn(&test_name!("headers_server")).await;
    for url in [
        "https://api.mistral.ai/v1/audio/speech",
        "wss://api.mistral.ai/v1/audio/transcriptions/realtime",
    ] {
        let headers = oidc::headers(Some(&client), url, oidc::API_KEY_MARKER)
            .await
            .unwrap();
        assert_eq!(headers["Authorization"], "Bearer id-token");
        assert_eq!(headers["x-external-organization-id"], "org");
    }
    let requests = fake_server::requests(&client).await;
    assert_eq!(
        requests
            .iter()
            .filter(|r| r["method"] == "setup/oidc-headers")
            .count(),
        2
    );
}

#[tokio::test]
async fn oidc_failure_never_falls_back_to_api_key() {
    let _sandbox = sandbox::sandbox();
    std::env::set_var("VIBE_CLI_OIDC_ENABLED", "1");
    std::env::set_var("MISTRAL_API_KEY", "ignored-api-key");
    let (client, _child) = fake_server::spawn(&test_name!("rejected_server")).await;
    assert!(oidc::headers(
        Some(&client),
        "https://api.mistral.ai",
        oidc::API_KEY_MARKER
    )
    .await
    .is_err());
    assert!(
        oidc::headers(None, "https://api.mistral.ai", oidc::API_KEY_MARKER)
            .await
            .is_err()
    );
}

#[tokio::test]
async fn api_key_mode_and_custom_providers_do_not_call_oidc_rpc() {
    let _sandbox = sandbox::sandbox();
    std::env::remove_var("VIBE_CLI_OIDC_ENABLED");
    std::env::set_var("MISTRAL_API_KEY", "api-key");
    assert_eq!(
        resolve_api_key("MISTRAL_API_KEY").as_deref(),
        Some("api-key")
    );
    let headers = oidc::headers(None, "https://api.mistral.ai", "api-key")
        .await
        .unwrap();
    assert_eq!(headers["Authorization"], "Bearer api-key");
    std::env::set_var("VIBE_CLI_OIDC_ENABLED", "true");
    std::env::set_var("CUSTOM_API_KEY", "custom-key");
    assert_eq!(
        resolve_api_key("CUSTOM_API_KEY").as_deref(),
        Some("custom-key")
    );
}

#[tokio::test]
async fn tts_posts_resolved_oidc_headers_and_receives_audio() {
    use std::io::{Read, Write};
    let _sandbox = sandbox::sandbox();
    std::env::set_var("VIBE_CLI_OIDC_ENABLED", "true");
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        socket
            .set_read_timeout(Some(std::time::Duration::from_secs(5)))
            .unwrap();
        let mut bytes = [0; 8192];
        let count = socket.read(&mut bytes).unwrap();
        let request = String::from_utf8_lossy(&bytes[..count]).to_lowercase();
        assert!(request.contains("authorization: bearer id-token"));
        assert!(request.contains("x-external-organization-id: org"));
        assert!(!request.contains("vibe-cli-oidc"));
        let body = r#"{"audio_data":"aGVsbG8="}"#;
        write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
    });
    let (client, _child) = fake_server::spawn(&test_name!("headers_server")).await;
    let cfg = vibe_rs::tts::SpeechConfig {
        name: "model".into(),
        voice: "voice".into(),
        response_format: "wav".into(),
        api_base: format!("http://{address}"),
        api_key_env_var: "MISTRAL_API_KEY".into(),
    };
    let audio = vibe_rs::tts::speak(
        &cfg,
        Some(&client),
        oidc::API_KEY_MARKER,
        "hello",
        json!({}),
    )
    .await
    .unwrap();
    assert_eq!(audio, b"hello");
    server.join().unwrap();
}

#[tokio::test]
#[allow(clippy::result_large_err)]
async fn transcription_handshake_sends_resolved_oidc_headers() {
    use futures::SinkExt;
    use tokio::sync::mpsc;
    use tokio_tungstenite::tungstenite::Message;
    let _sandbox = sandbox::sandbox();
    std::env::set_var("VIBE_CLI_OIDC_ENABLED", "true");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut websocket = tokio_tungstenite::accept_hdr_async(
            stream,
            |request: &tokio_tungstenite::tungstenite::handshake::server::Request, response| {
                assert_eq!(request.headers()["authorization"], "Bearer id-token");
                assert_eq!(request.headers()["x-external-organization-id"], "org");
                Ok(response)
            },
        )
        .await
        .unwrap();
        websocket
            .send(Message::Text(
                r#"{"type":"transcription.text.delta","text":"hello"}"#.into(),
            ))
            .await
            .unwrap();
        websocket
            .send(Message::Text(r#"{"type":"transcription.done"}"#.into()))
            .await
            .unwrap();
    });
    let (client, _child) = fake_server::spawn(&test_name!("headers_server")).await;
    let cfg = vibe_rs::voice::TranscriptionConfig {
        name: "model".into(),
        sample_rate: 16000,
        encoding: "pcm_s16le".into(),
        target_streaming_delay_ms: 200,
        api_base: format!("ws://{address}"),
        api_key_env_var: "MISTRAL_API_KEY".into(),
    };
    let (_chunks, chunks_rx) = mpsc::unbounded_channel();
    let (events, _events_rx) = mpsc::channel(8);
    assert!(vibe_rs::voice::transcribe::transcribe(
        &cfg,
        Some(&client),
        oidc::API_KEY_MARKER,
        16000,
        chunks_rx,
        &events
    )
    .await
    .unwrap());
    server.await.unwrap();
}

#[tokio::test]
async fn manual_key_submission_in_oidc_mode_sends_no_authenticated_request() {
    let _sandbox = sandbox::sandbox();
    let (client, _child) = fake_server::spawn(&test_name!("headers_server")).await;
    let mut wizard = OnboardingState::default();
    wizard.context.oidc_enabled = true;
    let close = actions::handle_onboarding_action(
        &mut wizard,
        &mut None,
        &client,
        Action::SubmitApiKey("ignored-key".into()),
    )
    .await;
    assert!(close.is_none());
    assert!(matches!(
        wizard.api_key_input.validation,
        vibe_rs::setup::wizard::ValidationState::Invalid(_)
    ));
    assert!(fake_server::requests(&client)
        .await
        .iter()
        .all(|r| r["method"] != "setup/store-credential"));
}
