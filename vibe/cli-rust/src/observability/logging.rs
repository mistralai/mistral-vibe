//! File logging into `$VIBE_HOME/logs/vibe-rs.log`.

use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use tracing::{Metadata, Subscriber};
use tracing_subscriber::fmt::format::Writer;
use tracing_subscriber::fmt::time::{FormatTime, SystemTime};
use tracing_subscriber::fmt::writer::MakeWriterExt as _;
use tracing_subscriber::layer::Layer;
use tracing_subscriber::prelude::*;
use tracing_subscriber::registry::LookupSpan;
use tracing_subscriber::Registry;

use crate::observability::level;
use crate::observability::rotating::{RotatingFile, DEFAULT_LOG_MAX_BYTES};
use crate::server::stderr;
use crate::utils::paths::vibe_home;

pub const LOG_FILE_NAME: &str = "vibe-rs.log";

pub fn log_file() -> Option<PathBuf> {
    vibe_home().map(|home| home.join("logs").join(LOG_FILE_NAME))
}

fn max_bytes() -> u64 {
    std::env::var("LOG_MAX_BYTES")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(DEFAULT_LOG_MAX_BYTES)
}

/// Installs the file layer and the Sentry bridge. A `None` path still composes
/// Sentry, which is inert until a client is bound.
pub fn init_file_logging(path: Option<&Path>) {
    level::apply_effective();
    let file = path
        .and_then(|path| RotatingFile::open(path, max_bytes()).ok())
        .map(file_layer);
    let _ = Registry::default()
        .with(file)
        .with(sentry_layer())
        .try_init();
}

/// `<rfc3339> <pid> <LEVEL> <target>: <message> <key>=<value>`
fn file_layer<S>(file: RotatingFile) -> impl Layer<S>
where
    S: Subscriber + for<'a> LookupSpan<'a>,
{
    // The level is checked on the writer rather than in `Layer::enabled`: tracing
    // caches a callsite's `Interest` forever, so a level read there would freeze at
    // the level of the first record.
    // Child stderr is already filtered by the app-server's own level, and is no
    // longer inherited, so the Rust threshold must not drop it a second time.
    let writer = Mutex::new(file).with_filter(|meta: &Metadata<'_>| {
        meta.target() == stderr::TARGET || level::enabled(meta.level())
    });
    tracing_subscriber::fmt::layer()
        .with_ansi(false)
        .with_timer(TimeAndPid)
        .with_writer(writer)
}

/// Timestamp plus pid, so concurrent instances stay distinguishable.
struct TimeAndPid;

impl FormatTime for TimeAndPid {
    fn format_time(&self, writer: &mut Writer<'_>) -> fmt::Result {
        SystemTime.format_time(writer)?;
        write!(writer, " {}", std::process::id())
    }
}

/// ERROR records become Sentry events; nothing else, since breadcrumbs are
/// dropped anyway (Python `_scrub_pii`).
fn sentry_layer<S>() -> impl Layer<S>
where
    S: Subscriber + for<'a> LookupSpan<'a>,
{
    use sentry::integrations::tracing::EventFilter;
    sentry::integrations::tracing::layer().event_filter(|metadata| match *metadata.level() {
        tracing::Level::ERROR => EventFilter::Event,
        _ => EventFilter::Ignore,
    })
}
