//! Exit-time peak RSS sampling.

#![cfg(unix)]

use std::sync::{Arc, Mutex};

#[test]
fn exit_rss_line_reports_the_peak() {
    let buf = SharedBuf::default();
    let subscriber = tracing_subscriber::fmt()
        .with_writer(buf.clone())
        .with_ansi(false)
        .with_max_level(tracing::Level::DEBUG)
        .finish();
    tracing::subscriber::with_default(subscriber, || {
        vibe_rs::observability::memory::log_exit_rss();
    });
    let line = String::from_utf8(buf.0.lock().unwrap().clone()).unwrap();
    assert!(line.contains("exit"), "got {line:?}");
    assert!(line.contains("peak_rss_kb"), "got {line:?}");
}

#[derive(Clone, Default)]
struct SharedBuf(Arc<Mutex<Vec<u8>>>);

impl std::io::Write for SharedBuf {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedBuf {
    type Writer = SharedBuf;

    fn make_writer(&'a self) -> Self::Writer {
        self.clone()
    }
}
