//! The update use-case (Python `update_notifier/update.py`).

use std::process::Stdio;

use tokio::io::AsyncReadExt;
use tokio::process::Command;

use super::cache::{UpdateCache, UpdateCacheRepository};
use super::gateway::{
    default_gateway_message, Update, UpdateGateway, UpdateGatewayError, UpdateSource,
};
use super::version::parse_version;

/// Python `UPDATE_CACHE_TTL_SECONDS`: how long a stored check stays fresh.
pub const UPDATE_CACHE_TTL_SECONDS: i64 = 24 * 60 * 60;

/// Python `UPDATE_COMMANDS`, run in order by `do_update`.
const UPDATE_COMMANDS: [&str; 2] = ["uv tool upgrade mistral-vibe", "brew upgrade mistral-vibe"];

/// The package the evidence words must name, so another line of the captured
/// output cannot pose as an upgrade.
const PROJECT_PACKAGE: &str = "mistral-vibe";

/// Python `UpdateAvailability`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UpdateAvailability {
    pub latest_version: String,
    pub should_notify: bool,
}

/// Python `UpdateError`: a failed check with its user-facing message. The
/// update cache's write failures are never surfaced — Python's
/// `FileSystemCacheStore.write_section` swallows the write's `OSError`, so
/// a read-only or malformed `cache.toml` degrades to "no cache" on both
/// clients instead of failing the check.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UpdateError {
    pub message: String,
}

/// One update command's run: exit status plus the captured stdout/stderr,
/// relayed to the user after the dialog (Python discards the output, so its
/// "was updated" line can claim an upgrade a no-op exit code never performed).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UpdateCommandOutcome {
    pub command: String,
    pub succeeded: bool,
    pub output: String,
}

/// Python `_describe_gateway_error`.
fn describe_gateway_error(error: &UpdateGatewayError) -> String {
    error
        .user_message
        .clone()
        .unwrap_or_else(|| default_gateway_message(error.cause).to_owned())
}

/// Python `_is_cache_fresh`.
fn is_cache_fresh(cache: &UpdateCache, now: i64) -> bool {
    cache.stored_at_timestamp > now - UPDATE_CACHE_TTL_SECONDS
}

/// Python `_get_cached_update_if_any`: a fresh cached newer version, without
/// re-notifying. Callers have already checked the entry's manager against
/// their own (`entry_source_trusted`), so an answer another install's check
/// wrote never reaches this.
fn cached_update_if_any(cache: &UpdateCache, current_version: &str) -> Option<UpdateAvailability> {
    let latest = parse_version(&cache.latest_version)?;
    let current = parse_version(current_version)?;
    if latest <= current {
        return None;
    }
    Some(UpdateAvailability {
        latest_version: cache.latest_version.clone(),
        should_notify: false,
    })
}

/// True when a cache entry's `source` was written by the reader's own
/// manager. A missing source is a legacy entry: every historical writer
/// before the tag existed answered from PyPI, so it counts only for PyPI
/// readers — managed installs re-check rather than trust it.
pub fn entry_source_trusted(entry_source: Option<&str>, source: UpdateSource) -> bool {
    match entry_source {
        None => source == UpdateSource::Pypi,
        Some(entry) => entry == source.as_str(),
    }
}

/// The manager whose answer this entry is, when the tag still belongs to it:
/// `stored_at_timestamp` is rewritten together with `latest_version` by every
/// writer, and a writer that does not know the tag (Python's key-merge
/// write) voids the pairing rather than let a stale tag speak for its own
/// answer. An unpaired tag reads as a legacy entry.
pub fn entry_answer_source(cache: &UpdateCache) -> Option<&str> {
    (cache.source_stored_at == Some(cache.stored_at_timestamp))
        .then_some(cache.source.as_deref())
        .flatten()
}

/// Python `_write_update_cache`: read-modify-write that keeps the seen and
/// dismissed fields. Divergence: the entry is stamped with the manager that
/// performed the check, replacing whatever install wrote the previous one.
/// A write that cannot persist is logged, never surfaced: Python's
/// `FileSystemCacheStore.write_section` swallows the write's `OSError`, so
/// the check's answer survives a read-only or malformed `cache.toml`.
fn write_update_cache<R: UpdateCacheRepository>(
    repository: &R,
    version: &str,
    now: i64,
    source: UpdateSource,
) {
    if let Err(err) = repository.modify(&mut |cache| {
        Some(UpdateCache {
            latest_version: version.to_owned(),
            stored_at_timestamp: now,
            seen_whats_new_version: cache
                .as_ref()
                .and_then(|cache| cache.seen_whats_new_version.clone()),
            dismissed_version: cache
                .as_ref()
                .and_then(|cache| cache.dismissed_version.clone()),
            source: Some(source.as_str().to_owned()),
            source_stored_at: Some(now),
        })
    }) {
        tracing::debug!(%err, "Failed to write the update cache");
    }
}

/// Python `get_update_if_available`. A non-forced check answers from a fresh
/// cache without contacting the gateway; every contacted outcome rewrites the
/// cache, and a write that cannot persist only costs the caching, not the
/// answer.
pub async fn get_update_if_available<G: UpdateGateway, R: UpdateCacheRepository>(
    gateway: &G,
    current_version: &str,
    repository: &R,
    now: i64,
    force_check: bool,
) -> Result<Option<UpdateAvailability>, UpdateError> {
    if parse_version(current_version).is_none() {
        return Ok(None);
    }
    let source = gateway.source();
    if !force_check {
        if let Some(cache) = repository.get() {
            // A fresh entry answers only when the reading install's own
            // manager wrote it; a foreign answer (another install's check)
            // falls through to this manager's gateway and is rewritten.
            if is_cache_fresh(&cache, now)
                && entry_source_trusted(entry_answer_source(&cache), source)
            {
                return Ok(cached_update_if_any(&cache, current_version));
            }
        }
    }

    let fetched: Result<Option<Update>, UpdateGatewayError> = gateway.fetch_update().await;
    let update = match fetched {
        Ok(update) => update,
        Err(error) => {
            write_update_cache(repository, current_version, now, source);
            return Err(UpdateError {
                message: describe_gateway_error(&error),
            });
        }
    };
    let Some(update) = update else {
        write_update_cache(repository, current_version, now, source);
        return Ok(None);
    };
    let Some(latest) = parse_version(&update.latest_version) else {
        return Ok(None);
    };
    let current = parse_version(current_version);
    if current.is_some_and(|current| latest <= current) {
        write_update_cache(repository, current_version, now, source);
        return Ok(None);
    }
    write_update_cache(repository, &update.latest_version, now, source);
    Ok(Some(UpdateAvailability {
        latest_version: update.latest_version,
        should_notify: true,
    }))
}

/// Python `get_pending_update_from_cache`: the cached newer version the user
/// has not dismissed yet, with the manager that wrote it. Callers gate the
/// answer on `entry_source_trusted` with their own install's manager — the
/// startup prompt defers that check until a pending update exists at all, so
/// a quiet cache costs no manager probes.
pub fn pending_update_from_cache<R: UpdateCacheRepository>(
    repository: &R,
    current_version: &str,
) -> Option<PendingUpdate> {
    let current = parse_version(current_version)?;
    let cache = repository.get()?;
    let latest = parse_version(&cache.latest_version)?;
    if latest <= current {
        return None;
    }
    if cache.dismissed_version.as_deref() == Some(cache.latest_version.as_str()) {
        return None;
    }
    let source = entry_answer_source(&cache).map(str::to_owned);
    Some(PendingUpdate {
        latest_version: cache.latest_version,
        source,
    })
}

/// A cached pending update and the manager whose answer it is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PendingUpdate {
    pub latest_version: String,
    pub source: Option<String>,
}

/// Cache a landed Homebrew revision as its bare release (`2.25.8+1` ->
/// `2.25.8`). The binary's version never includes the formula revision, so
/// leaving the local segment cached re-prompts on every launch while the
/// entry is still inside the TTL. A no-op for any other version, and for a
/// cache that has already moved on from this offer.
pub fn record_revision_upgrade<R: UpdateCacheRepository>(repository: &R, latest_version: &str) {
    let Some(installed) = revision_base(latest_version) else {
        return;
    };
    if let Err(err) = repository.modify(&mut |cache| {
        let mut cache = cache?;
        if cache.latest_version != latest_version {
            return None;
        }
        cache.latest_version = installed.to_owned();
        Some(cache)
    }) {
        tracing::debug!(%err, "Failed to record the installed revision");
    }
}

/// Inverse of brew's `_N` revision encoding: a single numeric local segment.
fn revision_base(version: &str) -> Option<&str> {
    let (base, revision) = version.rsplit_once('+')?;
    if base.is_empty() || base.contains('+') || revision.is_empty() {
        return None;
    }
    revision.bytes().all(|b| b.is_ascii_digit()).then_some(base)
}

/// Python `mark_update_as_dismissed`: a no-op without a stored cache. Python
/// lets the write's `OSError` raise into the caller, where `_show_update_prompt`
/// logs it; this is the only production caller, so the log lives here.
pub fn mark_update_as_dismissed<R: UpdateCacheRepository>(repository: &R, version: &str) {
    if let Err(err) = repository.modify(&mut |cache| {
        cache.map(|cache| UpdateCache {
            dismissed_version: Some(version.to_owned()),
            ..cache
        })
    }) {
        tracing::debug!(%err, "Failed to persist dismissed update");
    }
}

/// Python `do_update`: run each update command through the shell, stdin
/// detached and output captured for the evidence match and the result line.
/// A shell that cannot spawn is logged and skipped rather than aborting the
/// loop, unlike Python's `create_subprocess_shell`, which raises. Once
/// `terminate` is set, the running command is asked to stop gently and the
/// remaining ones are skipped.
pub async fn do_update(
    terminate: &tokio::sync::watch::Receiver<bool>,
) -> Vec<UpdateCommandOutcome> {
    run_update_commands(UPDATE_COMMANDS, |command| {
        spawn_update_command(command.to_owned(), terminate.clone())
    })
    .await
}

/// The command loop over an injectable runner, so tests never touch real
/// package managers. Each command runs even after a failure.
pub async fn run_update_commands<F, Fut>(
    commands: [&str; 2],
    mut run: F,
) -> Vec<UpdateCommandOutcome>
where
    F: FnMut(&str) -> Fut,
    Fut: std::future::Future<Output = UpdateCommandOutcome>,
{
    let mut outcomes = Vec::with_capacity(commands.len());
    for command in commands {
        outcomes.push(run(command).await);
    }
    outcomes
}

/// True when a command's output carries positive evidence of an upgrade:
/// a line leading with `Updated mistral-vibe` (uv's success line) or, after
/// brew's `==> ` header prefix, `Upgrading mistral-vibe` — case-insensitive.
/// Requiring the verb to lead the package name rejects brew's auto-update
/// preamble (`Updated 2 taps …`, `Updated Homebrew from …`) and its no-op
/// `Not upgrading mistral-vibe`. A bare exit 0 does not count either:
/// `uv tool upgrade` exits 0 on "Nothing to upgrade" too, and claiming
/// success there reports an update that never happened.
pub fn performed_upgrade(outcome: &UpdateCommandOutcome) -> bool {
    outcome.succeeded && outcome.output.lines().any(upgrade_evidence_in_line)
}

/// One line's evidence, case-insensitive: the upgrade verb must lead the
/// package name.
fn upgrade_evidence_in_line(line: &str) -> bool {
    let line = line.trim_start().to_lowercase();
    let line = line.strip_prefix("==> ").unwrap_or(&line);
    line.starts_with(&format!("updated {PROJECT_PACKAGE}"))
        || line.starts_with(&format!("upgrading {PROJECT_PACKAGE}"))
}

/// The offer's own manager's outcome, when one ran. `do_update` runs both
/// commands, so a verdict on the offer must read its manager's outcome, not
/// another install's. `None` when the offer's manager has no command (a
/// PyPI offer), where every outcome is the only signal there is.
fn own_outcome(
    outcomes: &[UpdateCommandOutcome],
    source: UpdateSource,
) -> Option<&UpdateCommandOutcome> {
    outcomes
        .iter()
        .find(|outcome| command_source(&outcome.command) == source)
}

/// True when a command upgraded — the dialog's Updated-vs-UpdateFailed
/// answer, settled by the offer's own manager's evidence so a leftover
/// other-manager install upgrading itself cannot pass as this one.
pub fn any_command_upgraded(outcomes: &[UpdateCommandOutcome], source: UpdateSource) -> bool {
    match own_outcome(outcomes, source) {
        Some(outcome) => performed_upgrade(outcome),
        None => outcomes.iter().any(performed_upgrade),
    }
}

/// The version the evidence line says the upgrade landed on — the offer's
/// own manager's answer, which can differ from the dialog's advertised
/// latest when a lagging brew formula or a re-resolved uv constraint
/// delivered something else. `None` when no evidence line carries a
/// `-> version` tail.
pub fn upgraded_version(outcomes: &[UpdateCommandOutcome], source: UpdateSource) -> Option<String> {
    let evidence =
        |outcome: &UpdateCommandOutcome| outcome.output.lines().find_map(version_after_arrow);
    match own_outcome(outcomes, source) {
        Some(outcome) => outcome.succeeded.then(|| evidence(outcome)).flatten(),
        None => outcomes
            .iter()
            .filter(|outcome| outcome.succeeded)
            .find_map(evidence),
    }
}

/// The version token after the last `->` of one evidence line, without the
/// leading `v` uv prints. Only a token that parses as a version counts.
fn version_after_arrow(line: &str) -> Option<String> {
    if !upgrade_evidence_in_line(line) {
        return None;
    }
    let (_, tail) = line.rsplit_once("->")?;
    let token = tail.split_whitespace().next()?.trim_start_matches('v');
    parse_version(token).is_some().then(|| token.to_owned())
}

/// Why no upgrade happened, for the single failure line: a no-op command's
/// own summary explains the real cause (nothing newer available) better
/// than a second manager's install error, so prefer it; then a failed
/// command's first line; then the fallback. The offer's own manager's line
/// wins over both, so a leftover install's no-op cannot mask the failure
/// of the command that matters.
pub fn not_updated_reason(outcomes: &[UpdateCommandOutcome], source: UpdateSource) -> String {
    if let Some(line) =
        own_outcome(outcomes, source).and_then(|outcome| outcome.output.lines().next())
    {
        return line.to_owned();
    }
    for outcome in outcomes.iter().filter(|outcome| outcome.succeeded) {
        if let Some(line) = outcome.output.lines().next() {
            return line.to_owned();
        }
    }
    for outcome in outcomes.iter().filter(|outcome| !outcome.succeeded) {
        if let Some(line) = outcome.output.lines().next() {
            return line.to_owned();
        }
    }
    "no update command reported an upgrade".to_owned()
}

/// The manager a command speaks for, from its command line: the offer's own
/// manager settles what a run meant, so an unrelated manager's exit 0 cannot
/// mask the failure of the command that matters.
pub fn command_source(command: &str) -> UpdateSource {
    if command.starts_with("uv ") {
        UpdateSource::Uv
    } else if command.starts_with("brew ") {
        UpdateSource::Brew
    } else {
        UpdateSource::Pypi
    }
}

/// True when a run that upgraded nothing was a no-op rather than a failure,
/// as settled by the offer's own manager: `do_update` runs both commands, so
/// the outcome of the manager whose answer the offer carries decides — its
/// exit 0 without upgrade evidence means the advertised version cannot be
/// delivered and the offer is stale. An offer without a matching command (a
/// PyPI offer has no update command) falls back to any command's exit 0:
/// with no manager to single out, that is the only no-op signal there is.
pub fn run_was_no_op(outcomes: &[UpdateCommandOutcome], source: UpdateSource) -> bool {
    match outcomes
        .iter()
        .find(|outcome| command_source(&outcome.command) == source)
    {
        Some(outcome) => outcome.succeeded,
        None => outcomes.iter().any(|outcome| outcome.succeeded),
    }
}

/// Force plain output from an update-related subprocess: the captured lines
/// are parsed for upgrade evidence (`Updated mistral-vibe …`) and for the
/// oracle's outdated-list, and ANSI escapes around the verb would defeat the
/// match. A user's `FORCE_COLOR`/`CLICOLOR_FORCE` forces uv to color even
/// into a pipe, so they are removed and `NO_COLOR` is set on top.
pub fn plain_output_env(command: &mut Command) -> &mut Command {
    command.env("NO_COLOR", "1");
    command.env_remove("FORCE_COLOR");
    command.env_remove("CLICOLOR_FORCE");
    command
}

/// One `sh -c` (or `cmd /C` on Windows) run with a detached stdin and its
/// stdout/stderr captured for the post-dialog relay. The pipes are drained
/// concurrently so a chatty manager cannot fill them and deadlock the wait.
/// `kill_on_drop` mirrors Python's CancelledError handling: abandoning the
/// wait kills the child instead of orphaning it. `HOMEBREW_NO_AUTO_UPDATE`
/// keeps brew's tap auto-update out of the dialog: it costs seconds of wait
/// and its `Updated N taps` line is not evidence of an upgrade. When
/// `terminate` is set mid-run, the child is asked to stop gently first (see
/// `terminate_child`). Public for the units tests that exercise the
/// child's environment and cancellation.
pub async fn spawn_update_command(
    command: String,
    mut terminate: tokio::sync::watch::Receiver<bool>,
) -> UpdateCommandOutcome {
    // Once cancellation was requested, no further command is started. The
    // borrow also marks the current value seen, so `changed()` below fires
    // only for a termination request that arrives while this command runs.
    if *terminate.borrow_and_update() {
        return UpdateCommandOutcome {
            succeeded: false,
            output: "update cancelled".to_owned(),
            command,
        };
    }
    let shell = if cfg!(windows) {
        std::env::var_os("COMSPEC").unwrap_or_else(|| "cmd".into())
    } else {
        std::ffi::OsString::from("sh")
    };
    let mut spawned = if cfg!(windows) {
        let mut command_line = Command::new(&shell);
        command_line.args(["/C", &command]);
        command_line
    } else {
        let mut shell_command = Command::new(&shell);
        shell_command.arg("-c").arg(&command);
        shell_command
    };
    plain_output_env(&mut spawned);
    let child = match spawned
        .env("HOMEBREW_NO_AUTO_UPDATE", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
    {
        Ok(child) => child,
        Err(err) => {
            tracing::warn!(%err, command, "update command could not run");
            return UpdateCommandOutcome {
                succeeded: false,
                output: err.to_string(),
                command,
            };
        }
    };
    wait_update_command(command, child, &mut terminate).await
}

/// Wait out one spawned update command, draining its pipes and honoring a
/// termination request: `select` between the natural exit and the terminate
/// signal, then always wait for the child so it is never orphaned. The pipe
/// readers watch the same signal, so a terminated run never waits on a pipe
/// a grandchild still holds.
async fn wait_update_command(
    command: String,
    mut child: tokio::process::Child,
    terminate: &mut tokio::sync::watch::Receiver<bool>,
) -> UpdateCommandOutcome {
    let stdout = tokio::spawn(read_pipe(child.stdout.take(), terminate.clone()));
    let stderr = tokio::spawn(read_pipe(child.stderr.take(), terminate.clone()));
    let status = tokio::select! {
        status = child.wait() => status,
        changed = terminate.changed() => {
            // `changed` resolves with an error once every sender is dropped:
            // nobody can request a termination then, so the command just runs
            // to its natural exit.
            if changed.is_ok() && *terminate.borrow() {
                terminate_child(&mut child).await;
            }
            child.wait().await
        }
    };
    let stdout = stdout.await.unwrap_or_default();
    let stderr = stderr.await.unwrap_or_default();
    match status {
        Ok(status) => UpdateCommandOutcome {
            succeeded: status.success(),
            output: combine_command_output(&stdout, &stderr),
            command,
        },
        Err(err) => {
            tracing::warn!(%err, command, "update command failed to report a status");
            UpdateCommandOutcome {
                succeeded: false,
                output: err.to_string(),
                command,
            }
        }
    }
}

/// Read one end of a captured pipe to its end. A detached reader task, so
/// the command never blocks on a full pipe while its exit status is awaited.
/// Only the direct child is signaled on termination; a grandchild that
/// inherited the pipe (a uv helper, Windows' `cmd /C`, which never execs)
/// could hold it open forever, so once termination is requested the drain is
/// bounded by `TERMINATED_PIPE_DRAIN` and whatever was read so far is kept.
async fn read_pipe<P: tokio::io::AsyncRead + Unpin>(
    pipe: Option<P>,
    mut terminate: tokio::sync::watch::Receiver<bool>,
) -> Vec<u8> {
    let mut buffer = Vec::new();
    let Some(mut pipe) = pipe else { return buffer };
    // A fresh clone reports the current value as a change; mark it seen so
    // `changed()` only fires on a real request.
    terminate.borrow_and_update();
    let mut chunk = [0u8; 8192];
    let mut drain_deadline: Option<tokio::time::Instant> = None;
    let mut terminate_armed = true;
    loop {
        let read = match drain_deadline {
            None if terminate_armed => tokio::select! {
                read = pipe.read(&mut chunk) => Some(read),
                changed = terminate.changed() => {
                    match changed {
                        Err(_) => terminate_armed = false,
                        Ok(()) if *terminate.borrow() => {
                            drain_deadline = Some(
                                tokio::time::Instant::now() + TERMINATED_PIPE_DRAIN,
                            );
                        }
                        Ok(()) => {}
                    }
                    continue;
                }
            },
            None => Some(pipe.read(&mut chunk).await),
            Some(deadline) => tokio::time::timeout_at(deadline, pipe.read(&mut chunk))
                .await
                .ok(),
        };
        match read {
            Some(Ok(0)) | Some(Err(_)) | None => break,
            Some(Ok(n)) => buffer.extend_from_slice(&chunk[..n]),
        }
    }
    buffer
}

/// How long a terminated command's pipes are still drained for the outcome
/// line before the read gives up on whatever holds them open.
const TERMINATED_PIPE_DRAIN: std::time::Duration = std::time::Duration::from_secs(2);

/// How long a terminated command has to exit on its own before the hard
/// kill. Python `_terminate` waits 2 seconds.
const TERMINATE_GRACE_SECONDS: std::time::Duration = std::time::Duration::from_secs(2);

/// Python `_terminate`: a SIGKILLed package manager can leave the tool
/// environment half-installed, so the child is asked to stop gently first —
/// SIGTERM on Unix, where uv and brew shut down cleanly — and the hard kill
/// only follows once the grace window runs out. On Windows Python's
/// `terminate()` is TerminateProcess, so the kill is immediate there.
async fn terminate_child(child: &mut tokio::process::Child) {
    #[cfg(unix)]
    {
        if let Some(pid) = child.id() {
            // SAFETY: a signal to the pid of a child this process spawned.
            unsafe { libc::kill(pid as libc::pid_t, libc::SIGTERM) };
        }
        let deadline = tokio::time::Instant::now() + TERMINATE_GRACE_SECONDS;
        while tokio::time::Instant::now() < deadline {
            if matches!(child.try_wait(), Ok(Some(_))) {
                return;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
    }
    let _ = child.start_kill();
}

/// Interleave the captured streams the way the terminal would have shown
/// them: stdout first, then stderr, each trimmed of the trailing newline.
fn combine_command_output(stdout: &[u8], stderr: &[u8]) -> String {
    let mut text = String::new();
    for stream in [stdout, stderr] {
        let stream = String::from_utf8_lossy(stream);
        let stream = stream.trim_end();
        if !stream.is_empty() {
            if !text.is_empty() {
                text.push('\n');
            }
            text.push_str(stream);
        }
    }
    text
}
