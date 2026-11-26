//! The uv oracle over a fake `uv` on PATH: a uv without `--outdated` falls back to PyPI.
#![cfg(unix)]

use std::os::unix::fs::PermissionsExt;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use vibe_rs::update_notifier::gateway::{PyPIUpdateGateway, UpdateGateway};
use vibe_rs::update_notifier::uv_oracle::UvOracleGateway;

const OLD_UV_STDERR: &str = "error: unexpected argument '--outdated' found\n\nUsage: uv tool list [OPTIONS]\n\nFor more information, try '--help'.";

fn install_fake_uv(stderr: &str) -> tempfile::TempDir {
    let bin = tempfile::tempdir().unwrap();
    let uv = bin.path().join("uv");
    std::fs::write(
        &uv,
        format!("#!/bin/sh\ncat >&2 <<'EOF'\n{stderr}\nEOF\nexit 2\n"),
    )
    .unwrap();
    std::fs::set_permissions(&uv, std::fs::Permissions::from_mode(0o755)).unwrap();
    let path = std::env::var("PATH").unwrap_or_default();
    std::env::set_var("PATH", format!("{}:{path}", bin.path().display()));
    bin
}

async fn serve_pypi_release(version: &str) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let body = format!(
        r#"{{"versions": ["{version}"], "files": [{{"filename": "mistral_vibe-{version}-py3-none-any.whl", "yanked": false}}]}}"#
    );
    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut request = [0; 4096];
        let _ = socket.read(&mut request).await.unwrap();
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        socket.write_all(response.as_bytes()).await.unwrap();
    });
    format!("http://{address}")
}

#[tokio::test]
async fn a_uv_without_outdated_answers_from_pypi() {
    let _bin = install_fake_uv(OLD_UV_STDERR);
    let gateway = UvOracleGateway {
        fallback: PyPIUpdateGateway::with_base_url(
            "mistral-vibe",
            &serve_pypi_release("9.9.9").await,
        ),
    };
    let update = gateway.fetch_update().await.unwrap().unwrap();
    assert_eq!(update.latest_version, "9.9.9");
}

#[tokio::test]
async fn other_uv_failures_stay_errors() {
    let _bin = install_fake_uv("error: Request failed after 3 retries");
    let gateway = UvOracleGateway {
        fallback: PyPIUpdateGateway::with_base_url(
            "mistral-vibe",
            &serve_pypi_release("9.9.9").await,
        ),
    };
    let error = gateway.fetch_update().await.unwrap_err();
    assert!(error.user_message.unwrap().contains("Request failed"));
}
