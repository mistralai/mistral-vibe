//! `@` file entries: index or directory listing, and their insertion (quoted when needed; Tab on a folder descends).

use super::CompletionEntry;
use crate::app::App;

const FILE_MATCH_LIMIT: usize = 100;

const QUOTES: [char; 2] = ['"', '\''];

/// Whether `query` (the text after `@`) still completes: a quoted query may hold spaces until its closing quote.
pub(super) fn is_open(query: &str) -> bool {
    match query.chars().next() {
        Some(quote) if QUOTES.contains(&quote) => !query[1..].contains(quote),
        _ => !query.contains(' '),
    }
}

/// Byte offset of the `@` opening the mention typed in `before`: an unclosed
/// quoted mention wins, and an `@` inside a quoted mention (`@'node_modules/@types/'`) stays text.
pub(super) fn mention_start(before: &str) -> Option<usize> {
    let mut last = None;
    let mut rest = 0;
    while let Some(offset) = before[rest..].find('@') {
        let at = rest + offset;
        last = Some(at);
        rest = at + 1;
        let Some(quote) = before[rest..].chars().next().filter(|c| QUOTES.contains(c)) else {
            continue;
        };
        match before[rest + 1..].find(quote) {
            Some(close) => rest += close + 2,
            None => return Some(at),
        }
    }
    last
}

/// The path typed after `@`, without its opening quote.
pub(super) fn unquoted(query: &str) -> &str {
    query.strip_prefix(QUOTES).unwrap_or(query)
}

/// Replace `start..end` with the mention for `label`. Tab on a folder other
/// than the typed one leaves the caret inside it and returns true: the popup
/// stays open on its entries.
pub(super) fn insert(app: &mut App, label: &str, start: usize, end: usize, descend: bool) -> bool {
    let path = label.strip_prefix('@').unwrap_or(label);
    let mention = crate::paste_path::image_path_mention(path);
    let input = &app.chat_input.input;
    let closing = input[start + 1..end]
        .chars()
        .next()
        .filter(|quote| QUOTES.contains(quote) && input[end..].starts_with(*quote));
    let current = path == unquoted(&input[start + 1..end]).replace('\\', "/");
    let end = end + closing.map_or(0, char::len_utf8);
    let is_dir = path.ends_with('/');
    if descend && is_dir && !current {
        let quote = mention[1..].chars().next().filter(|c| QUOTES.contains(c));
        app.chat_input.input.replace_range(start..end, &mention);
        app.chat_input.cursor = start + mention.len() - quote.map_or(0, char::len_utf8);
        app.chat_input.anchor = None;
        app.chat_input.sync_mentions_at(start);
        super::input_changed(app);
        return true;
    }
    let mut text = mention.clone();
    if !is_dir {
        text.push(' ');
    }
    app.chat_input.input.replace_range(start..end, &text);
    app.chat_input.cursor = start + text.len();
    app.chat_input.sync_mentions_at(start);
    app.chat_input.add_mention(start, start + mention.len());
    false
}

/// Entries for the `@` query in `start..end`: a rooted path lists its directory, anything else searches the index.
pub(super) fn entries(app: &mut App, start: usize, end: usize) -> Vec<CompletionEntry> {
    let query = unquoted(&app.chat_input.input[start + 1..end]).replace('\\', "/");
    crate::dir_complete::entries(app, &query, FILE_MATCH_LIMIT)
        .unwrap_or_else(|| index_entries(app, &query))
        .into_iter()
        .map(|label| CompletionEntry {
            label,
            description: String::new(),
            origin: None,
        })
        .collect()
}

/// Index matches; a `dir/` query naming an indexed directory lists that directory first.
fn index_entries(app: &App, query: &str) -> Vec<String> {
    let index = &app.completion.files.index;
    let dir = query.strip_suffix('/').filter(|dir| index.is_dir(dir));
    let current = dir.map(|_| format!("@{query}"));
    let rest = FILE_MATCH_LIMIT - usize::from(current.is_some());
    current
        .into_iter()
        .chain(index.matching(query, rest))
        .collect()
}
