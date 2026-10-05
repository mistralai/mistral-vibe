//! Pasted file lists: image placeholders, Finder clipboard file URLs and
//! off-reducer path existence probes.

use std::fs::File;
use std::io::{Read, Seek};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Instant;

use crate::app::App;
use crate::edit_history::Snapshot;
use crate::paste_image::{self, Event};
use crate::{completion_manager, input, paste_path};

const FILE_URLS_SCRIPT: &str = r#"ObjC.import("AppKit");
const options = $.NSDictionary.dictionaryWithObjectForKey(true, $.NSPasteboardURLReadingFileURLsOnlyKey);
const urls = $.NSPasteboard.generalPasteboard.readObjectsForClassesOptions($([$.NSURL]), options);
urls.js.map((url) => url.path.js).join("\n")"#;

/// Check an inserted path list on a blocking job; it stays as typed without a job slot.
pub fn request_probe(app: &mut App, start: usize, raw: String, candidates: Vec<String>) {
    let fallback = Event::Probed {
        start,
        raw: raw.clone(),
        paths: None,
    };
    let job = move || Event::Probed {
        start,
        raw,
        paths: paste_path::paths_resolve(&candidates, |path| path.exists()).then_some(candidates),
    };
    spawn_job(app, job, fallback);
}

/// Run `job` on a bounded blocking slot and deliver its event; false without a slot.
pub(crate) fn spawn_job(
    app: &mut App,
    job: impl FnOnce() -> Event + Send + 'static,
    fallback: Event,
) -> bool {
    let Some(tx) = app
        .paste_image
        .tx
        .clone()
        .filter(|_| app.paste_image.has_slot())
    else {
        return false;
    };
    app.paste_image.in_flight += 1;
    let pending = app.commit_started();
    tokio::spawn(async move {
        let event = tokio::task::spawn_blocking(job).await.unwrap_or(fallback);
        crate::input::deliver(Some(tx), event, &pending).await;
    });
    true
}

/// Swap the inserted paste, if still in the composer, for its resolved mentions:
/// image placeholders and `@path` mentions.
pub fn apply_probed(app: &mut App, start: usize, raw: &str, paths: Option<Vec<String>>) {
    let Some(paths) = paths.filter(|_| !input::composer_hidden(app)) else {
        return;
    };
    app.chat_input.normalize_positions();
    let input = &app.chat_input.input;
    let unmoved = input.get(start..).is_some_and(|rest| rest.starts_with(raw));
    let found = unmoved
        .then_some(start)
        .or_else(|| find_standalone(input, raw));
    let Some(start) = found else {
        return;
    };
    let before = Snapshot::capture(&app.chat_input);
    let used = app.view.transcript.highest_image_label();
    let tokens: Vec<String> = paths
        .iter()
        .map(|path| match paste_path::is_image_path(path) {
            true => app.chat_input.pasted_images.register(path, used),
            false => paste_path::image_path_mention(path),
        })
        .collect();
    let joined = tokens.join(" ");
    let end = start + raw.len();
    let input = &app.chat_input.input;
    let rest = format!("{}{}", &input[..start], &input[end..]);
    let replacement = paste_path::with_image_mention_boundaries(&rest, start, &joined);
    app.chat_input.input.replace_range(start..end, &replacement);
    let replaced = (start, end, replacement.len());
    app.chat_input.cursor = shift(app.chat_input.cursor, replaced);
    app.chat_input.anchor = app.chat_input.anchor.map(|pos| shift(pos, replaced));
    app.chat_input.sync_mentions_at(start);
    add_mentions(
        app,
        start + replacement.find(&joined).unwrap_or_default(),
        &tokens,
    );
    completion_manager::input_changed(app);
    app.chat_input.record_edit(before, true, Instant::now());
}

fn find_standalone(input: &str, raw: &str) -> Option<usize> {
    input
        .match_indices(raw)
        .map(|(index, _)| index)
        .find(|&index| {
            let before = input[..index].chars().next_back();
            let after = input[index + raw.len()..].chars().next();
            before.is_none_or(char::is_whitespace) && after.is_none_or(char::is_whitespace)
        })
}

fn shift(pos: usize, (start, end, len): (usize, usize, usize)) -> usize {
    match pos {
        pos if pos >= end => pos - (end - start) + len,
        pos if pos > start => start + len,
        pos => pos,
    }
}

/// Insert clipboard images at the caret as one edit.
pub fn apply_image_files(app: &mut App, paths: Vec<PathBuf>) {
    if paste_image::rejects_images(app) || input::composer_hidden(app) {
        return;
    }
    app.chat_input.normalize_positions();
    let before = Snapshot::capture(&app.chat_input);
    input::reset_history_state(app);
    crate::long_paste::dismiss(app);
    let paths: Vec<String> = paths
        .iter()
        .map(|path| path.to_string_lossy().into_owned())
        .collect();
    insert_images(app, &paths);
    app.chat_input.scroll = None;
    app.chat_input.record_edit(before, true, Instant::now());
    completion_manager::input_changed(app);
}

/// Insert `paths` at the caret, replacing the selection, as `[Image #N]`
/// placeholder mentions, or as raw paths where the mode names no images.
pub fn insert_images(app: &mut App, paths: &[String]) {
    app.chat_input.widen_selection();
    let input = &app.chat_input;
    let at = input
        .anchor
        .map_or(input.cursor, |anchor| anchor.min(input.cursor));
    let names = input.mode.names_images();
    let used = app.view.transcript.highest_image_label();
    let tokens: Vec<String> = match names {
        true => paths
            .iter()
            .map(|path| app.chat_input.pasted_images.register(path, used))
            .collect(),
        false => paths.to_vec(),
    };
    let (start, _) = paste_image::insert_image_token(
        &mut app.chat_input.input,
        &mut app.chat_input.cursor,
        &mut app.chat_input.anchor,
        &tokens.join(" "),
    );
    app.chat_input.sync_mentions_at(at);
    if names {
        add_mentions(app, start, &tokens);
    }
}

/// Track space-separated `tokens` starting at byte `start` as mentions.
fn add_mentions(app: &mut App, mut start: usize, tokens: &[String]) {
    for token in tokens {
        app.chat_input.add_mention(start, start + token.len());
        start += token.len() + 1;
    }
}

/// Keep only the image files of a Finder copy, bounded like pasted path lists.
pub fn image_files(files: Vec<PathBuf>) -> Vec<PathBuf> {
    files
        .into_iter()
        .filter(|path| paste_path::is_image_path(&path.to_string_lossy()))
        .take(paste_path::MAX_PASTED_PATHS)
        .collect()
}

pub fn read_clipboard_files() -> Vec<PathBuf> {
    if !paste_image::is_supported() {
        return Vec::new();
    }
    read_macos_files().unwrap_or_default()
}

pub fn parse_clipboard_files(output: &str) -> Vec<PathBuf> {
    output
        .lines()
        .filter(|line| !line.is_empty())
        .map(PathBuf::from)
        .collect()
}

fn read_macos_files() -> Option<Vec<PathBuf>> {
    let mut output: File = tempfile::tempfile().ok()?;
    let mut command = Command::new("osascript");
    command.args(["-l", "JavaScript", "-e", FILE_URLS_SCRIPT]);
    if !paste_image::run_with_timeout(&mut command, Stdio::from(output.try_clone().ok()?)) {
        return None;
    }
    let mut text = String::new();
    output.rewind().ok()?;
    output.read_to_string(&mut text).ok()?;
    Some(parse_clipboard_files(&text))
}
