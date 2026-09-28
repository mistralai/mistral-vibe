//! Realtime transcription over WebSocket (Python `MistralTranscribeClient`).

use base64::Engine as _;
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::mpsc::{Sender, UnboundedReceiver};
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;

use super::{TranscriptionConfig, VoiceEvent};

/// The server rejects a flush with no audio; that is a benign empty recording.
const EMPTY_RECORDING_MARKER: &str = "before sending any audio bytes";

/// Stream `chunks` to the realtime endpoint and forward text deltas to the UI.
pub async fn transcribe(
    cfg: &TranscriptionConfig,
    api_key: &str,
    sample_rate: u32,
    chunks: UnboundedReceiver<Vec<u8>>,
    events: &Sender<VoiceEvent>,
) -> Result<(), String> {
    let url = build_url(&cfg.api_base, &cfg.name);
    let mut req = url
        .as_str()
        .into_client_request()
        .map_err(|e| format!("Bad transcription URL: {e}"))?;
    let auth = format!("Bearer {api_key}")
        .parse()
        .map_err(|_| "Invalid API key".to_string())?;
    req.headers_mut().insert("authorization", auth);

    let (ws, _) = connect_async(req)
        .await
        .map_err(|e| format!("Transcription connection failed: {e}"))?;
    let (mut write, mut read) = ws.split();

    let session_update = json!({
        "type": "session.update",
        "session": {
            "audio_format": {"encoding": cfg.encoding, "sample_rate": sample_rate},
            "target_streaming_delay_ms": cfg.target_streaming_delay_ms,
        }
    });
    write
        .send(Message::Text(session_update.to_string()))
        .await
        .map_err(|e| format!("Transcription send failed: {e}"))?;

    let send = tokio::spawn(async move {
        let mut chunks = chunks;
        while let Some(chunk) = chunks.recv().await {
            let audio = base64::engine::general_purpose::STANDARD.encode(&chunk);
            let msg = json!({"type": "input_audio.append", "audio": audio}).to_string();
            if write.send(Message::Text(msg)).await.is_err() {
                return;
            }
        }
        let flush = json!({"type": "input_audio.flush"}).to_string();
        let _ = write.send(Message::Text(flush)).await;
        let end = json!({"type": "input_audio.end"}).to_string();
        let _ = write.send(Message::Text(end)).await;
    });

    let result = read_events(&mut read, events).await;
    send.abort();
    result
}

/// Drain server events until `transcription.done`, an error, or the socket closes.
async fn read_events<S>(read: &mut S, events: &Sender<VoiceEvent>) -> Result<(), String>
where
    S: StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
{
    let mut got_text = false;
    while let Some(Ok(msg)) = read.next().await {
        let text = match msg {
            Message::Text(t) => t,
            Message::Close(_) => break,
            _ => continue,
        };
        let Ok(value) = serde_json::from_str::<Value>(text.as_str()) else {
            continue;
        };
        match value.get("type").and_then(Value::as_str) {
            Some("transcription.text.delta") => {
                if let Some(delta) = value.get("text").and_then(Value::as_str) {
                    got_text = true;
                    let _ = events.try_send(VoiceEvent::TextDelta(delta.to_string()));
                }
            }
            Some("transcription.done") => break,
            Some("error") => {
                let message = value
                    .pointer("/error/message")
                    .and_then(Value::as_str)
                    .unwrap_or("Transcription error");
                if message.contains(EMPTY_RECORDING_MARKER) {
                    break;
                }
                return Err(message.to_string());
            }
            _ => {}
        }
    }
    if !got_text {
        let _ = events.try_send(VoiceEvent::Notice("No speech detected".to_string()));
    }
    Ok(())
}

/// `{api_base}/v1/audio/transcriptions/realtime?model=…`, forcing the ws scheme.
fn build_url(api_base: &str, model: &str) -> String {
    let base = api_base.trim_end_matches('/');
    let base = match base.strip_prefix("https://") {
        Some(rest) => format!("wss://{rest}"),
        None => match base.strip_prefix("http://") {
            Some(rest) => format!("ws://{rest}"),
            None => base.to_string(),
        },
    };
    format!("{base}/v1/audio/transcriptions/realtime?model={model}")
}
