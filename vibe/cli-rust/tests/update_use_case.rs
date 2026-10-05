//! The update use-case over a fake gateway and a temp VIBE_HOME repository.

// The env guard must span the awaits that touch VIBE_HOME; the single-threaded
// test runtime serializes them, so the lock is never contended across await.
#![allow(clippy::await_holding_lock)]

use std::sync::Mutex;

use vibe_rs::update_notifier::cache::{
    FileSystemUpdateCacheRepository, UpdateCache, UpdateCacheRepository,
};
use vibe_rs::update_notifier::gateway::{
    Update, UpdateGateway, UpdateGatewayCause, UpdateGatewayError, UpdateSource,
};
use vibe_rs::update_notifier::update::{
    any_command_upgraded, command_source, entry_source_trusted, get_update_if_available,
    mark_update_as_dismissed, not_updated_reason, pending_update_from_cache, performed_upgrade,
    record_revision_upgrade, run_update_commands, run_was_no_op, spawn_update_command,
    upgraded_version, PendingUpdate, UpdateCommandOutcome, UpdateError,
};

/// Guards VIBE_HOME, which is process-global across this binary's tests.
static ENV_LOCK: Mutex<()> = Mutex::new(());

const NOW: i64 = 1_000_000;

struct TempHome(std::path::PathBuf);

impl TempHome {
    fn new(label: &str) -> Self {
        let home = std::env::temp_dir().join(format!(
            "vibe-rs-update-use-case-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(&home).unwrap();
        std::env::set_var("VIBE_HOME", &home);
        Self(home)
    }

    fn seed(&self, update_cache: &UpdateCache) {
        let mut text = String::new();
        text.push_str("[update_cache]\n");
        text.push_str(&format!(
            "latest_version = \"{}\"\n",
            update_cache.latest_version
        ));
        text.push_str(&format!(
            "stored_at_timestamp = {}\n",
            update_cache.stored_at_timestamp
        ));
        if let Some(seen) = &update_cache.seen_whats_new_version {
            text.push_str(&format!("seen_whats_new_version = \"{seen}\"\n"));
        }
        if let Some(dismissed) = &update_cache.dismissed_version {
            text.push_str(&format!("dismissed_version = \"{dismissed}\"\n"));
        }
        if let Some(source) = &update_cache.source {
            text.push_str(&format!("source = \"{source}\"\n"));
        }
        if let Some(stored_at) = update_cache.source_stored_at {
            text.push_str(&format!("source_stored_at = {stored_at}\n"));
        }
        std::fs::write(self.0.join("cache.toml"), text).unwrap();
    }

    fn read(&self) -> Option<UpdateCache> {
        FileSystemUpdateCacheRepository.get()
    }
}

impl Drop for TempHome {
    fn drop(&mut self) {
        std::env::remove_var("VIBE_HOME");
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// A gateway whose answer is fixed and whose calls are counted.
struct FakeGateway {
    result: Result<Option<Update>, UpdateGatewayError>,
    source: UpdateSource,
    calls: std::cell::Cell<u32>,
}

impl FakeGateway {
    fn with(update: Option<Update>) -> Self {
        Self {
            result: Ok(update),
            source: UpdateSource::Pypi,
            calls: std::cell::Cell::new(0),
        }
    }

    fn with_source(update: Option<Update>, source: UpdateSource) -> Self {
        Self {
            result: Ok(update),
            source,
            calls: std::cell::Cell::new(0),
        }
    }

    fn failing(error: UpdateGatewayError) -> Self {
        Self {
            result: Err(error),
            source: UpdateSource::Pypi,
            calls: std::cell::Cell::new(0),
        }
    }

    fn calls(&self) -> u32 {
        self.calls.get()
    }
}

impl UpdateGateway for FakeGateway {
    async fn fetch_update(&self) -> Result<Option<Update>, UpdateGatewayError> {
        self.calls.set(self.calls.get() + 1);
        match &self.result {
            Ok(update) => Ok(update.clone()),
            Err(error) => Err(error.clone()),
        }
    }

    fn source(&self) -> UpdateSource {
        self.source
    }
}

fn latest(version: &str) -> Update {
    Update {
        latest_version: version.to_owned(),
    }
}

fn cache(latest_version: &str, stored_at_timestamp: i64) -> UpdateCache {
    UpdateCache {
        latest_version: latest_version.to_owned(),
        stored_at_timestamp,
        seen_whats_new_version: None,
        dismissed_version: None,
        source: None,
        source_stored_at: None,
    }
}

fn sourced(latest_version: &str, stored_at_timestamp: i64, source: &str) -> UpdateCache {
    UpdateCache {
        source: Some(source.to_owned()),
        source_stored_at: Some(stored_at_timestamp),
        ..cache(latest_version, stored_at_timestamp)
    }
}

async fn check(
    gateway: &FakeGateway,
    current: &str,
    force: bool,
) -> Result<Option<vibe_rs::update_notifier::update::UpdateAvailability>, UpdateError> {
    get_update_if_available(
        gateway,
        current,
        &FileSystemUpdateCacheRepository,
        NOW,
        force,
    )
    .await
}

#[tokio::test]
async fn fresh_cache_answers_without_the_gateway() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("fresh-cache");
    home.seed(&cache("2.99.0", NOW - 1000));

    let gateway = FakeGateway::with(None);
    let result = check(&gateway, "2.25.6", false).await.unwrap().unwrap();
    assert_eq!(result.latest_version, "2.99.0");
    assert!(!result.should_notify);
    assert_eq!(gateway.calls(), 0);
}

#[tokio::test]
async fn stale_cache_or_force_check_hits_the_gateway() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("stale-cache");
    home.seed(&cache("2.99.0", NOW - 3 * 24 * 3600));

    let gateway = FakeGateway::with(Some(latest("3.0.0")));
    let result = check(&gateway, "2.25.6", false).await.unwrap().unwrap();
    assert_eq!(result.latest_version, "3.0.0");
    assert!(result.should_notify);
    assert_eq!(gateway.calls(), 1);

    // Force ignores even a fresh cache.
    home.seed(&cache("2.99.0", NOW - 1000));
    let gateway = FakeGateway::with(Some(latest("3.0.0")));
    let result = check(&gateway, "2.25.6", true).await.unwrap().unwrap();
    assert_eq!(result.latest_version, "3.0.0");
    assert!(result.should_notify);
    assert_eq!(gateway.calls(), 1);
}

#[tokio::test]
async fn no_update_or_older_updates_write_the_cache_with_current() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("no-update");

    let gateway = FakeGateway::with(None);
    assert!(matches!(check(&gateway, "2.25.6", true).await, Ok(None)));
    assert_eq!(home.read(), Some(sourced("2.25.6", NOW, "pypi")));

    let gateway = FakeGateway::with(Some(latest("2.25.6")));
    assert!(matches!(check(&gateway, "2.25.6", true).await, Ok(None)));
    assert_eq!(home.read(), Some(sourced("2.25.6", NOW, "pypi")));

    let gateway = FakeGateway::with(Some(latest("2.25.6-rc1")));
    // The prerelease hack: hyphens become local version metadata, which
    // outranks the bare release — like Python, this notifies.
    let result = check(&gateway, "2.25.6", true).await.unwrap().unwrap();
    assert_eq!(result.latest_version, "2.25.6-rc1");
    assert!(result.should_notify);
}

#[tokio::test]
async fn gateway_errors_write_the_cache_and_carry_the_message() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("gateway-error");
    home.seed(&UpdateCache {
        latest_version: "2.99.0".to_owned(),
        stored_at_timestamp: NOW - 10,
        seen_whats_new_version: Some("2.25.6".to_owned()),
        dismissed_version: None,
        source: None,
        source_stored_at: None,
    });

    let error = UpdateGatewayError {
        cause: UpdateGatewayCause::TooManyRequests,
        user_message: None,
    };
    let gateway = FakeGateway::failing(error);
    let result = match check(&gateway, "2.25.6", true).await {
        Err(error) => error,
        other => panic!("expected a gateway failure, got {other:?}"),
    };
    assert_eq!(
        result.message,
        "Rate limit exceeded while checking for updates."
    );

    // The cache write keeps the seen/dismissed fields, like Python's
    // read-modify-write.
    let read = home.read().unwrap();
    assert_eq!(read.latest_version, "2.25.6");
    assert_eq!(read.stored_at_timestamp, NOW);
    assert_eq!(read.seen_whats_new_version.as_deref(), Some("2.25.6"));

    // A gateway error with a user message wins over the default text.
    let error = UpdateGatewayError {
        cause: UpdateGatewayCause::Unknown,
        user_message: Some("custom".to_owned()),
    };
    let gateway = FakeGateway::failing(error);
    let result = match check(&gateway, "2.25.6", true).await {
        Err(error) => error,
        other => panic!("expected a gateway failure, got {other:?}"),
    };
    assert_eq!(result.message, "custom");
}

/// A repository whose writes always fail, like a full or read-only disk.
struct FailingWriteRepository;

impl UpdateCacheRepository for FailingWriteRepository {
    fn get(&self) -> Option<UpdateCache> {
        None
    }

    fn set(&self, _cache: &UpdateCache) -> std::io::Result<()> {
        Err(std::io::Error::other("disk full"))
    }
}

#[tokio::test]
async fn a_check_that_cannot_persist_the_cache_still_answers() {
    let gateway = FakeGateway::with(Some(latest("3.0.0")));
    let result =
        get_update_if_available(&gateway, "2.25.6", &FailingWriteRepository, NOW, true).await;
    // Python's `FileSystemCacheStore.write_section` swallows the write's
    // `OSError`, so a read-only or malformed cache.toml costs the caching,
    // never the check's answer.
    assert_eq!(
        result,
        Ok(Some(vibe_rs::update_notifier::update::UpdateAvailability {
            latest_version: "3.0.0".to_owned(),
            should_notify: true
        }))
    );
}

#[tokio::test]
async fn a_gateway_error_answers_despite_the_failed_cache_write() {
    let error = UpdateGatewayError {
        cause: UpdateGatewayCause::TooManyRequests,
        user_message: None,
    };
    let gateway = FakeGateway::failing(error);
    let result =
        get_update_if_available(&gateway, "2.25.6", &FailingWriteRepository, NOW, true).await;
    // The write failure is logged inside; the gateway's own error is what the
    // forced check reports.
    let error = match result {
        Err(error) => error,
        other => panic!("expected a gateway failure, got {other:?}"),
    };
    assert_eq!(
        error.message,
        "Rate limit exceeded while checking for updates."
    );
}

#[tokio::test]
async fn unparsable_gateway_answers_write_no_cache() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("unparsable-answer");

    let gateway = FakeGateway::with(Some(latest("junk")));
    assert!(matches!(check(&gateway, "2.25.6", true).await, Ok(None)));
    assert_eq!(home.read(), None);
}

#[tokio::test]
async fn pending_update_from_cache_respects_dismissal_and_carries_its_source() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("pending");
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.25.6"),
        None
    );

    home.seed(&sourced("2.99.0", NOW, "uv"));
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.25.6"),
        Some(PendingUpdate {
            latest_version: "2.99.0".to_owned(),
            source: Some("uv".to_owned()),
        })
    );
    // Not newer than current.
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.99.0"),
        None
    );

    mark_update_as_dismissed(&FileSystemUpdateCacheRepository, "2.99.0");
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.25.6"),
        None
    );
}

/// `2.25.8_1` is cached as `2.25.8+1`, which stays newer than the binary's
/// `CARGO_PKG_VERSION` after `brew upgrade`. Recording the upgrade stores the
/// bare release, so the next launch is not prompted and a fresh cache does
/// not ask brew to put the revision back.
#[tokio::test]
async fn a_landed_brew_revision_is_not_pending_on_the_next_launch() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("revision-upgrade");
    let mut entry = sourced("2.25.8+1", NOW - 1000, "brew");
    entry.seen_whats_new_version = Some("2.25.0".to_owned());
    entry.dismissed_version = Some("2.0.0".to_owned());
    home.seed(&entry);
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.25.8")
            .map(|pending| pending.latest_version),
        Some("2.25.8+1".to_owned())
    );

    record_revision_upgrade(&FileSystemUpdateCacheRepository, "2.25.8+1");
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.25.8"),
        None
    );
    let gateway = FakeGateway::with_source(Some(latest("2.25.8+1")), UpdateSource::Brew);
    assert_eq!(check(&gateway, "2.25.8", false).await.unwrap(), None);
    assert_eq!(gateway.calls(), 0);

    let cache = home.read().unwrap();
    assert_eq!(cache.latest_version, "2.25.8");
    assert_eq!(cache.stored_at_timestamp, NOW - 1000);
    assert_eq!(cache.seen_whats_new_version.as_deref(), Some("2.25.0"));
    assert_eq!(cache.dismissed_version.as_deref(), Some("2.0.0"));
    assert_eq!(cache.source.as_deref(), Some("brew"));
    assert_eq!(cache.source_stored_at, Some(NOW - 1000));

    // A revision on a newer release is the bare release the new binary reports.
    home.seed(&sourced("2.25.9+1", NOW - 1000, "brew"));
    record_revision_upgrade(&FileSystemUpdateCacheRepository, "2.25.9+1");
    assert_eq!(home.read().unwrap().latest_version, "2.25.9");
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.25.9"),
        None
    );

    // Anything that is not a formula revision stays pending.
    home.seed(&sourced("2.26.0", NOW, "brew"));
    record_revision_upgrade(&FileSystemUpdateCacheRepository, "2.26.0");
    assert_eq!(home.read().unwrap().latest_version, "2.26.0");
    home.seed(&sourced("2.25.8+abc", NOW, "brew"));
    record_revision_upgrade(&FileSystemUpdateCacheRepository, "2.25.8+abc");
    assert_eq!(home.read().unwrap().latest_version, "2.25.8+abc");
}

#[test]
fn a_source_is_trusted_only_by_the_manager_that_wrote_it() {
    // A matching source counts, whatever the reader is.
    assert!(entry_source_trusted(Some("uv"), UpdateSource::Uv));
    assert!(entry_source_trusted(Some("pypi"), UpdateSource::Pypi));
    assert!(entry_source_trusted(Some("brew"), UpdateSource::Brew));
    // A foreign source never counts, even for PyPI readers.
    assert!(!entry_source_trusted(Some("uv"), UpdateSource::Pypi));
    assert!(!entry_source_trusted(Some("pypi"), UpdateSource::Uv));
    assert!(!entry_source_trusted(Some("brew"), UpdateSource::Uv));
    // A legacy untagged entry was written by a PyPI-era check, so only a
    // PyPI reader trusts it; a managed install re-checks instead.
    assert!(entry_source_trusted(None, UpdateSource::Pypi));
    assert!(!entry_source_trusted(None, UpdateSource::Uv));
    assert!(!entry_source_trusted(None, UpdateSource::Brew));
}

#[tokio::test]
async fn a_stale_tag_left_by_a_foreign_write_does_not_answer_the_check() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("stale-tag");
    home.seed(&sourced("2.99.0", NOW - 1000, "uv"));

    // The tag still belongs to this entry, so the uv reader trusts it.
    let gateway = FakeGateway::with_source(None, UpdateSource::Uv);
    let result = check(&gateway, "2.25.6", false).await.unwrap().unwrap();
    assert_eq!(result.latest_version, "2.99.0");
    assert_eq!(gateway.calls(), 0);

    // Python's key-merge write moves the version and the timestamp but knows
    // nothing of the tag, which stays behind pointing at the old write.
    vibe_rs::utils::cache_store::write_section(
        "update_cache",
        &[
            (
                "latest_version",
                vibe_rs::utils::cache_store::SectionValue::Str("2.99.1".to_owned()),
            ),
            (
                "stored_at_timestamp",
                vibe_rs::utils::cache_store::SectionValue::Int(NOW),
            ),
        ],
    )
    .unwrap();

    // The pending-update query no longer carries the stale tag, and the uv
    // reader re-checks rather than trust the entry as its own answer.
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.25.6"),
        Some(PendingUpdate {
            latest_version: "2.99.1".to_owned(),
            source: None,
        })
    );
    let gateway = FakeGateway::with_source(Some(latest("2.25.7")), UpdateSource::Uv);
    let result = check(&gateway, "2.25.6", false).await.unwrap().unwrap();
    assert_eq!(result.latest_version, "2.25.7");
    assert_eq!(gateway.calls(), 1);
    let rewritten = home.read().unwrap();
    assert_eq!(
        rewritten.source_stored_at,
        Some(rewritten.stored_at_timestamp)
    );
}

#[tokio::test]
async fn a_voided_tag_reads_as_a_legacy_entry() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("voided-tag");
    // Python's cache write voids the pairing with a -1 sentinel, which no
    // stored timestamp can ever match.
    home.seed(&UpdateCache {
        latest_version: "2.99.0".to_owned(),
        stored_at_timestamp: NOW,
        seen_whats_new_version: None,
        dismissed_version: None,
        source: Some("uv".to_owned()),
        source_stored_at: Some(-1),
    });

    // The pending query carries no manager for the entry...
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.25.6")
            .map(|pending| { pending.source }),
        Some(None)
    );
    // ...so a uv reader re-checks instead of trusting the stale tag.
    let gateway = FakeGateway::with_source(None, UpdateSource::Uv);
    assert!(check(&gateway, "2.25.6", false).await.unwrap().is_none());
    assert_eq!(gateway.calls(), 1);
    // The re-check re-stamps a pair the reader can trust again.
    let rewritten = home.read().unwrap();
    assert_eq!(
        rewritten.source_stored_at,
        Some(rewritten.stored_at_timestamp)
    );
    assert_eq!(rewritten.source.as_deref(), Some("uv"));
}

#[tokio::test]
async fn a_fresh_cache_from_another_manager_does_not_answer_the_check() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("foreign-cache");
    home.seed(&sourced("2.99.0", NOW - 1000, "uv"));

    // The uv answer is fresh but not this reader's; the gateway answers.
    let gateway = FakeGateway::with(Some(latest("2.25.7")));
    let result = check(&gateway, "2.25.6", false).await.unwrap().unwrap();
    assert_eq!(result.latest_version, "2.25.7");
    assert!(result.should_notify);
    assert_eq!(gateway.calls(), 1);
    // The rewrite stamps this check's manager over the foreign one.
    assert_eq!(home.read().unwrap().source.as_deref(), Some("pypi"));
}

#[tokio::test]
async fn a_legacy_untagged_cache_still_answers_a_pypi_reader() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("legacy-cache");
    home.seed(&cache("2.99.0", NOW - 1000));

    let gateway = FakeGateway::with(None);
    let result = check(&gateway, "2.25.6", false).await.unwrap().unwrap();
    assert_eq!(result.latest_version, "2.99.0");
    assert_eq!(gateway.calls(), 0);
}

#[tokio::test]
async fn every_contacted_check_stamps_its_manager_on_the_cache() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("stamp");
    home.seed(&sourced("2.99.0", NOW - 1000, "pypi"));

    // A no-update answer from uv still rewrites the cache as uv's.
    let gateway = FakeGateway::with_source(None, UpdateSource::Uv);
    assert!(matches!(check(&gateway, "2.25.6", true).await, Ok(None)));
    let cache = home.read().unwrap();
    assert_eq!(cache.latest_version, "2.25.6");
    assert_eq!(cache.source.as_deref(), Some("uv"));
    // The seen and dismissed fields survive the cross-manager rewrite.
    assert_eq!(cache.seen_whats_new_version, None);
    assert_eq!(cache.dismissed_version, None);
}

#[test]
fn dismissal_without_a_cache_is_a_no_op() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let _home = TempHome::new("dismiss-no-cache");
    mark_update_as_dismissed(&FileSystemUpdateCacheRepository, "2.99.0");
    assert_eq!(
        pending_update_from_cache(&FileSystemUpdateCacheRepository, "2.25.6"),
        None
    );
}

#[tokio::test]
async fn do_update_loop_runs_every_command_and_keeps_their_output() {
    let commands = ["first", "second"];
    // Every command runs even after a failure, and its output is kept for the
    // result line.
    let seen = std::sync::Mutex::new(Vec::new());
    let run = |command: &str| {
        seen.lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push(command.to_owned());
        std::future::ready(UpdateCommandOutcome {
            command: command.to_owned(),
            succeeded: true,
            output: format!("ran {command}"),
        })
    };
    let outcomes = run_update_commands(commands, run).await;
    assert_eq!(
        seen.lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .as_slice(),
        ["first", "second"]
    );
    assert_eq!(
        outcomes
            .iter()
            .map(|outcome| outcome.output.as_str())
            .collect::<Vec<_>>(),
        ["ran first", "ran second"]
    );
}

#[cfg(unix)]
#[tokio::test]
async fn update_commands_run_with_plain_colors() {
    // A user's FORCE_COLOR/CLICOLOR_FORCE would color uv's success line and
    // defeat the evidence match; the child must see plain colors instead.
    let (_, terminate) = tokio::sync::watch::channel(false);
    let outcome = spawn_update_command(
        "echo \"$NO_COLOR:${FORCE_COLOR-unset}:${CLICOLOR_FORCE-unset}\"".to_owned(),
        terminate,
    )
    .await;
    assert!(outcome.succeeded);
    assert_eq!(outcome.output, "1:unset:unset");
}

#[cfg(unix)]
#[tokio::test]
async fn a_terminated_update_command_stops_within_the_grace_window() {
    // Ctrl+C/Q must not SIGKILL a mid-install package manager: the child is
    // asked to stop gently first, so a well-behaved command exits well before
    // the hard kill's 2s grace would end.
    let (terminate_update, terminate) = tokio::sync::watch::channel(false);
    let running = tokio::spawn(spawn_update_command("sleep 30".to_owned(), terminate));
    tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    terminate_update.send(true).expect("receiver alive");
    let settled = tokio::time::timeout(std::time::Duration::from_secs(5), running).await;
    let outcome = settled
        .expect("the terminated command settles promptly")
        .expect("no join failure");
    assert!(!outcome.succeeded);
}

#[cfg(unix)]
#[tokio::test]
async fn a_terminated_update_command_does_not_wait_on_a_grandchild_pipe() {
    // Only the direct child is signaled; a grandchild that inherited the
    // pipes (uv helper, Windows' cmd /C) could hold them open forever. The
    // drain after termination is bounded, so the outcome settles even while
    // the grandchild still owns the pipe — far before its own exit.
    let (terminate_update, terminate) = tokio::sync::watch::channel(false);
    let running = tokio::spawn(spawn_update_command(
        "sleep 15 & wait".to_owned(),
        terminate,
    ));
    tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    terminate_update.send(true).expect("receiver alive");
    let settled = tokio::time::timeout(std::time::Duration::from_secs(8), running).await;
    let outcome = settled
        .expect("the terminated command settles without the grandchild's EOF")
        .expect("no join failure");
    assert!(!outcome.succeeded);
}

#[test]
fn command_source_is_read_from_the_command_line() {
    assert_eq!(
        command_source("uv tool upgrade mistral-vibe"),
        UpdateSource::Uv
    );
    assert_eq!(
        command_source("brew upgrade mistral-vibe"),
        UpdateSource::Brew
    );
    assert_eq!(command_source("something else"), UpdateSource::Pypi);
}

#[test]
fn a_no_op_is_settled_by_the_offers_own_manager() {
    let uv_no_op = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Nothing to upgrade".to_owned(),
    };
    let brew_failed = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: false,
        output: "Error: brew failed".to_owned(),
    };
    // A leftover uv tool's exit 0 must not mask the brew failure behind a
    // brew offer, and the offer's own no-op still counts.
    assert!(!run_was_no_op(
        &[uv_no_op.clone(), brew_failed.clone()],
        UpdateSource::Brew
    ));
    assert!(run_was_no_op(
        &[uv_no_op.clone(), brew_failed.clone()],
        UpdateSource::Uv
    ));
    // The manager that failed keeps the offer alive: not a no-op.
    let uv_failed = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: false,
        output: "error: no such tool".to_owned(),
    };
    assert!(!run_was_no_op(&[uv_failed, brew_failed], UpdateSource::Uv));
    // A PyPI offer has no command of its own: any exit 0 is the only no-op
    // signal there is.
    assert!(run_was_no_op(&[uv_no_op], UpdateSource::Pypi));
}

#[test]
fn upgraded_requires_evidence_in_the_output_not_a_bare_exit_code() {
    // uv's and brew's success lines, verbatim.
    let updated = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Updated mistral-vibe v2.25.4 -> v2.25.5".to_owned(),
    };
    assert!(performed_upgrade(&updated));
    let upgrading = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "==> Upgrading mistral-vibe".to_owned(),
    };
    assert!(performed_upgrade(&upgrading));
    // A no-op exit 0 does not count: uv prints "Nothing to upgrade".
    let noop = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Nothing to upgrade".to_owned(),
    };
    assert!(!performed_upgrade(&noop));
    // Neither does a no-op that merely mentions the word, like uv's
    // "Nothing was updated".
    let already = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Nothing was updated".to_owned(),
    };
    assert!(!performed_upgrade(&already));
    let updated_line = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Updated mistral-vibe v1 -> v2".to_owned(),
    };
    assert!(performed_upgrade(&updated_line));
    // brew's auto-update preamble names taps or Homebrew itself, not the
    // package: not evidence on an exit-0 no-op.
    let tap_update = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Updated 2 taps (homebrew/core and homebrew/cask).\nNo packages to upgrade"
            .to_owned(),
    };
    assert!(!performed_upgrade(&tap_update));
    let brew_update = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Updated Homebrew from 4.5.0 to 4.6.0".to_owned(),
    };
    assert!(!performed_upgrade(&brew_update));
    // brew's no-op line names the package but the verb is negated.
    let skipped = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output:
            "Not upgrading mistral-vibe, the installed version is not below the minimum version"
                .to_owned(),
    };
    assert!(!performed_upgrade(&skipped));
    // Neither does a failed run, update-shaped or not.
    let failed = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: false,
        output: "Error: mistral-vibe not installed".to_owned(),
    };
    assert!(!performed_upgrade(&failed));
}

#[test]
fn upgraded_version_reads_the_managers_own_answer() {
    // uv's success line: the version after the arrow, minus the leading v.
    let uv = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Updated mistral-vibe v2.25.7 -> v2.25.8".to_owned(),
    };
    assert_eq!(
        upgraded_version(std::slice::from_ref(&uv), UpdateSource::Uv),
        Some("2.25.8".to_owned())
    );
    // The evidence line without an arrow (brew's header) carries no version.
    let brew = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "==> Upgrading mistral-vibe".to_owned(),
    };
    assert_eq!(
        upgraded_version(std::slice::from_ref(&brew), UpdateSource::Brew),
        None
    );
    // A brew offer reads brew's answer, not uv's arrow tail.
    assert_eq!(
        upgraded_version(&[brew.clone(), uv.clone()], UpdateSource::Brew),
        None
    );
    assert_eq!(
        upgraded_version(&[brew.clone(), uv.clone()], UpdateSource::Uv),
        Some("2.25.8".to_owned())
    );
    // A PyPI offer has no command of its own: any evidence counts.
    assert_eq!(
        upgraded_version(&[uv], UpdateSource::Pypi),
        Some("2.25.8".to_owned())
    );
    let failed = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: false,
        output: "Updated mistral-vibe v2.25.7 -> v2.25.8".to_owned(),
    };
    assert_eq!(upgraded_version(&[failed], UpdateSource::Uv), None);
    // An unparsable token after the arrow is not a version.
    let junk = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Updated mistral-vibe v2.25.7 -> not-a-version".to_owned(),
    };
    assert_eq!(upgraded_version(&[junk], UpdateSource::Uv), None);
}

#[test]
fn not_updated_reason_prefers_a_noop_summary_over_a_later_error() {
    let noop = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Nothing to upgrade".to_owned(),
    };
    let failed = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: false,
        output: "==> Downloading Homebrew API data\nError: mistral-vibe not installed".to_owned(),
    };
    // A uv offer reads uv's line, whatever brew said.
    assert_eq!(
        not_updated_reason(&[noop.clone(), failed.clone()], UpdateSource::Uv),
        "Nothing to upgrade"
    );
    // A brew offer reads brew's own line, not uv's foreign no-op summary.
    assert_eq!(
        not_updated_reason(&[noop.clone(), failed.clone()], UpdateSource::Brew),
        "==> Downloading Homebrew API data"
    );
    // Without a no-op, the first failed command's first line is the reason.
    assert_eq!(
        not_updated_reason(std::slice::from_ref(&failed), UpdateSource::Brew),
        "==> Downloading Homebrew API data"
    );
    // A PyPI offer has no command of its own: the old ordering applies.
    assert_eq!(
        not_updated_reason(&[noop.clone(), failed], UpdateSource::Pypi),
        "Nothing to upgrade"
    );
    // A no-op alone still explains itself.
    assert_eq!(
        not_updated_reason(&[noop], UpdateSource::Uv),
        "Nothing to upgrade"
    );
    // Nothing ran or nothing printed: the fallback.
    assert_eq!(
        not_updated_reason(
            &[UpdateCommandOutcome {
                command: "nothing".to_owned(),
                succeeded: false,
                output: String::new(),
            }],
            UpdateSource::Uv
        ),
        "no update command reported an upgrade"
    );
}

#[test]
fn any_command_upgraded_answers_updated_only_on_evidence() {
    let upgraded = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Updated mistral-vibe v2.25.4 -> v2.25.5".to_owned(),
    };
    let noop = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Nothing to upgrade".to_owned(),
    };
    let failed = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: false,
        output: "Error: mistral-vibe not installed".to_owned(),
    };
    assert!(!any_command_upgraded(
        &[noop.clone(), failed.clone()],
        UpdateSource::Uv
    ));
    assert!(!any_command_upgraded(
        &[noop.clone(), failed.clone()],
        UpdateSource::Brew
    ));
    assert!(any_command_upgraded(&[failed, upgraded], UpdateSource::Uv));
}

#[test]
fn a_foreign_manager_upgrade_does_not_settle_the_offer() {
    // A leftover uv tool upgraded itself while the offer is brew's: the
    // dialog must not claim this Vibe was updated.
    let uv_upgraded = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "Updated mistral-vibe v2.25.4 -> v2.25.5".to_owned(),
    };
    let brew_failed = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: false,
        output: "Error: mistral-vibe not installed".to_owned(),
    };
    assert!(!any_command_upgraded(
        &[uv_upgraded.clone(), brew_failed.clone()],
        UpdateSource::Brew
    ));
    assert_eq!(
        upgraded_version(&[uv_upgraded, brew_failed], UpdateSource::Brew),
        None
    );
    // A leftover brew install upgraded while the offer is uv's: same verdict.
    let brew_upgraded = UpdateCommandOutcome {
        command: "brew upgrade mistral-vibe".to_owned(),
        succeeded: true,
        output: "==> Upgrading mistral-vibe".to_owned(),
    };
    let uv_failed = UpdateCommandOutcome {
        command: "uv tool upgrade mistral-vibe".to_owned(),
        succeeded: false,
        output: "error: failed to fetch mistral-vibe".to_owned(),
    };
    assert!(!any_command_upgraded(
        &[uv_failed.clone(), brew_upgraded.clone()],
        UpdateSource::Uv
    ));
    // A PyPI offer has no command of its own: any evidence settles it.
    assert!(any_command_upgraded(
        &[uv_failed, brew_upgraded],
        UpdateSource::Pypi
    ));
}
