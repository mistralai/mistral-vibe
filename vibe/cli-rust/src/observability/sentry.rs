//! Crash reporting. Mirrors `vibe/observability/sentry.py`.
//!
//! The config gate (`enableTelemetry`) only arrives over the wire at Ready, so
//! the startup window before it — spawn, handshake, startup failure — is
//! covered by an explicit operator opt-in: `init_pre_ready` binds a client only
//! when `SENTRY_DSN` is set, so default runs (compiled-in DSN `None`) stay
//! silent. `init_sentry` at Ready then rebinds or drops that client according
//! to the config gate, and `flush` ships on every exit path.

use std::collections::BTreeMap;
use std::sync::Mutex;

use sentry::protocol::{Event, Value};
use sentry::ClientOptions;

use crate::observability::scrub::scrub_paths;

/// Compiled-in DSN, injected at release time like the Python `_CLI_SENTRY_DSN`.
const CLI_SENTRY_DSN: Option<&str> = None;
/// Own server name: Rust is a delivery surface beside `vibe-cli`, not the same one.
const SERVER_NAME: &str = "vibe-rs";

/// POSIX EIO. `io::Error` maps it to `ErrorKind::Uncategorized`, which cannot be
/// matched, so the raw number is the stable handle.
const EIO: i32 = 5;

static GUARD: Mutex<Option<sentry::ClientInitGuard>> = Mutex::new(None);

/// Locks `GUARD` recovering from poison: poison means a panic under the lock,
/// the payload is still valid, and flush-on-panic matters most.
fn lock() -> std::sync::MutexGuard<'static, Option<sentry::ClientInitGuard>> {
    GUARD.lock().unwrap_or_else(|err| err.into_inner())
}

/// The bind verdict for one startup phase; `bind_decision` maps the inputs.
#[derive(Debug, PartialEq, Eq)]
pub enum Decision {
    /// Bind (or rebind) a client.
    Bind,
    /// Unbind the bound client: the config gate arrived disabled.
    Drop,
    /// Nothing to do: no opt-in and no config verdict yet.
    Hold,
}

/// Maps (env `SENTRY_DSN`, config gate) to the bind decision for one phase: a
/// `None` gate is the pre-ready window where the wire answer is still unknown,
/// `Some` is Ready, where the gate is authoritative.
pub fn bind_decision(env_dsn: Option<&str>, config_enabled: Option<bool>) -> Decision {
    match config_enabled {
        None if env_dsn.is_some() => Decision::Bind,
        None => Decision::Hold,
        Some(true) => Decision::Bind,
        Some(false) => Decision::Drop,
    }
}

/// `SENTRY_DSN` overrides the compiled-in DSN, so a dev build can point at its
/// own project without a rebuild. Checked here rather than left to the SDK's
/// `apply_defaults` so a DSN-less run never pays for `sentry::init`.
fn env_dsn() -> Option<String> {
    std::env::var("SENTRY_DSN")
        .ok()
        .filter(|value| !value.is_empty())
}

fn dsn() -> Option<String> {
    env_dsn().or_else(|| CLI_SENTRY_DSN.map(str::to_owned))
}

/// The terminal went away or the disk filled: the environment broke, not Vibe.
/// Matched on `ErrorKind`/errno rather than the `Display` text, which `strerror`
/// localizes on Unix and which Windows words differently again.
pub fn is_environmental(error: &anyhow::Error) -> bool {
    error
        .chain()
        .filter_map(|cause| cause.downcast_ref::<std::io::Error>())
        .any(|io| {
            matches!(
                io.kind(),
                std::io::ErrorKind::BrokenPipe | std::io::ErrorKind::StorageFull
            ) || io.raw_os_error() == Some(EIO)
        })
}

fn before_send(mut event: Event<'static>) -> Option<Event<'static>> {
    scrub_event(&mut event);
    Some(event)
}

/// Drops IP and breadcrumbs, then scrubs every path in the event.
pub fn scrub_event(event: &mut Event<'static>) {
    if let Some(user) = event.user.as_mut() {
        user.ip_address = None;
    }
    event.breadcrumbs.values.clear();
    scrub_named_fields(event);
    scrub_every_field(event);
}

/// The floor: applied unconditionally so a failed round-trip below still leaves
/// the fields most likely to carry a path scrubbed.
fn scrub_named_fields(event: &mut Event<'static>) {
    event.message = event.message.as_deref().map(scrub_paths);
    for exception in &mut event.exception {
        exception.value = exception.value.as_deref().map(scrub_paths);
    }
    for value in event.extra.values_mut() {
        scrub_value(value);
    }
}

/// Python's `_scrub_pii` walks the whole event dict. Do the same over the
/// serialized form, which is what reaches Sentry, so stack frame `abs_path`,
/// threads, contexts and tags are covered rather than enumerated.
fn scrub_every_field(event: &mut Event<'static>) {
    let Ok(mut value) = serde_json::to_value(&*event) else {
        return;
    };
    scrub_value(&mut value);
    if let Ok(scrubbed) = serde_json::from_value(value) {
        *event = scrubbed;
    }
}

fn scrub_value(value: &mut Value) {
    match value {
        Value::String(text) => *text = scrub_paths(text),
        Value::Array(items) => items.iter_mut().for_each(scrub_value),
        Value::Object(fields) => fields.values_mut().for_each(scrub_value),
        _ => {}
    }
}

/// Startup-window opt-in: binds a client before the app-server spawns, so
/// spawn, handshake and startup failures reach Sentry. Only `SENTRY_DSN`
/// gates this; `init_sentry` at Ready rebinds or drops the client per config.
pub fn init_pre_ready(headless: bool, tags: BTreeMap<String, String>) -> bool {
    if bind_decision(env_dsn().as_deref(), None) != Decision::Bind {
        return false;
    }
    init_sentry(true, headless, tags)
}

/// Returns whether a client was bound; false when disabled or DSN-less.
pub fn init_sentry(enabled: bool, headless: bool, tags: BTreeMap<String, String>) -> bool {
    if !enabled {
        // The config gate is authoritative: a pre-ready opt-in client unbinds.
        drop_client();
        return false;
    }
    let Some(dsn) = dsn().and_then(|dsn| dsn.parse().ok()) else {
        return false;
    };
    let mut options = ClientOptions::default();
    options.dsn = Some(dsn);
    // `environment` stays unset: the SDK fills it from `SENTRY_ENVIRONMENT`,
    // falling back to development/production by build profile.
    options.release = Some(format!("vibe@{}", env!("CARGO_PKG_VERSION")).into());
    options.server_name = Some(SERVER_NAME.into());
    options.send_default_pii = false;
    options.attach_stacktrace = true;
    options.before_send = Some(std::sync::Arc::new(before_send));
    let guard = sentry::init(options);
    if !guard.is_enabled() {
        return false;
    }
    let global_tags: Vec<(String, String)> = [
        ("headless".to_owned(), headless.to_string()),
        ("os".to_owned(), std::env::consts::OS.to_owned()),
        ("arch".to_owned(), std::env::consts::ARCH.to_owned()),
    ]
    .into_iter()
    .chain(tags)
    .collect();
    sentry::configure_scope(|scope| {
        for (key, value) in global_tags {
            scope.set_tag(&key, value);
        }
    });
    // The hub binds inside `sentry::init`, so the swap never leaves it
    // clientless; the old guard drops only once the lock releases, so its
    // drain cannot stall other lock users or re-enter the held lock.
    let old = lock().replace(guard);
    drop(old);
    true
}

/// Unbinds the client: the guard's drop drains (bounded by the SDK's shutdown
/// timeout) and closes the transport, so later captures are no-ops. The drain
/// runs outside the lock for the same reason as the swap in `init_sentry`.
fn drop_client() {
    let old = lock().take();
    drop(old);
}

/// Whether a client is currently bound; asserts the lifecycle in tests.
pub fn is_bound() -> bool {
    lock().is_some()
}

/// Logs every panic to the file sink; the tracing bridge turns it into a Sentry
/// event once a client is bound. Installed before the terminal goes raw.
pub fn install_panic_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let payload = info
            .payload()
            .downcast_ref::<&str>()
            .map(|value| (*value).to_owned())
            .or_else(|| info.payload().downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "panic".to_owned());
        let location = info
            .location()
            .map(|at| format!("{}:{}", at.file(), at.line()))
            .unwrap_or_default();
        tracing::error!(
            vibe_boundary = "panic",
            fatal = true,
            location = %location,
            "vibe-rs panicked: {payload}"
        );
        flush();
        previous(info);
    }));
}

/// Blocks briefly so a fatal path still ships its event, like the Python flush.
pub fn flush() {
    if let Some(client) = lock().as_ref() {
        client.flush(Some(std::time::Duration::from_secs(5)));
    }
}
