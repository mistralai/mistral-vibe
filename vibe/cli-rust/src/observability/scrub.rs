//! Path scrubbing, a port of the Python `_HOME_RE` and `_PATH_RE` passes.

pub const FILTERED: &str = "[Filtered]";

/// Collapses a bare home root, then rewrites any remaining path to
/// `[Filtered]/<basename>`. Same two ordered passes as the Python `scrub_paths`.
pub fn scrub_paths(value: &str) -> String {
    let home = replace_all(value, match_home_root);
    replace_all(&home, match_path)
}

/// A pattern: given the text and an offset, the end of the match and what
/// replaces it.
type Matcher = fn(&[char], usize) -> Option<(usize, String)>;

/// Scans for matches the way `re.sub` does: leftmost, then continue after it.
fn replace_all(value: &str, matcher: Matcher) -> String {
    let chars: Vec<char> = value.chars().collect();
    let mut out = String::with_capacity(value.len());
    let mut index = 0;
    while index < chars.len() {
        if !preceded_by_word_or_separator(&chars, index) {
            if let Some((end, replacement)) = matcher(&chars, index) {
                out.push_str(&replacement);
                index = end;
                continue;
            }
        }
        out.push(chars[index]);
        index += 1;
    }
    out
}

/// The `(?<![\w\\/])` lookbehind both patterns open with.
fn preceded_by_word_or_separator(chars: &[char], index: usize) -> bool {
    index
        .checked_sub(1)
        .is_some_and(|previous| is_word(chars[previous]) || is_separator(chars[previous]))
}

fn is_word(ch: char) -> bool {
    ch.is_alphanumeric() || ch == '_'
}

fn is_separator(ch: char) -> bool {
    ch == '/' || ch == '\\'
}

/// `[^\s"'`\\/]`, the component character class.
fn is_component(ch: char) -> bool {
    !ch.is_whitespace() && !is_separator(ch) && !matches!(ch, '"' | '\'' | '`')
}

/// `(?:[A-Za-z]:[\\/]|~?[\\/])`, returning the index just past it.
fn match_start(chars: &[char], index: usize) -> Option<usize> {
    if chars.get(index).is_some_and(|ch| ch.is_ascii_alphabetic())
        && chars.get(index + 1) == Some(&':')
        && chars.get(index + 2).is_some_and(|ch| is_separator(*ch))
    {
        return Some(index + 3);
    }
    let after_tilde = if chars.get(index) == Some(&'~') {
        index + 1
    } else {
        index
    };
    chars
        .get(after_tilde)
        .is_some_and(|ch| is_separator(*ch))
        .then_some(after_tilde + 1)
}

/// `[^\s"'`\\/]+`, returning the index just past it.
fn match_component(chars: &[char], index: usize) -> Option<usize> {
    let end = chars[index..]
        .iter()
        .take_while(|ch| is_component(**ch))
        .count()
        + index;
    (end > index).then_some(end)
}

/// `[^\s"'`\\/]+(?:[ ]+[^\s"'`\\/]+)*`: a component that may contain spaces.
fn match_spaced_component(chars: &[char], index: usize) -> Option<usize> {
    let mut end = match_component(chars, index)?;
    loop {
        let spaces = chars[end..].iter().take_while(|ch| **ch == ' ').count();
        let Some(next) = (spaces > 0)
            .then(|| match_component(chars, end + spaces))
            .flatten()
        else {
            return Some(end);
        };
        end = next;
    }
}

/// `_HOME_RE`: a `Users`/`home` root that is not itself a prefix of a longer path.
fn match_home_root(chars: &[char], index: usize) -> Option<(usize, String)> {
    let after_start = match_start(chars, index)?;
    let after_root = ["Users", "home"].iter().find_map(|root| {
        let end = after_start + root.chars().count();
        let matches = chars
            .get(after_start..end)?
            .iter()
            .copied()
            .eq(root.chars());
        (matches && chars.get(end).is_some_and(|ch| is_separator(*ch))).then_some(end + 1)
    })?;
    let end = match_component(chars, after_root)?;
    (!continues_into_a_deeper_path(chars, end)).then(|| (end, FILTERED.to_owned()))
}

/// The `(?!(?:[ ]+[^\s"'`\\/]+)*+[\\/][^\s"'`\\/])` lookahead: another segment follows.
fn continues_into_a_deeper_path(chars: &[char], index: usize) -> bool {
    let mut end = index;
    loop {
        if chars.get(end).is_some_and(|ch| is_separator(*ch))
            && chars.get(end + 1).is_some_and(|ch| is_component(*ch))
        {
            return true;
        }
        let spaces = chars[end..].iter().take_while(|ch| **ch == ' ').count();
        let Some(next) = (spaces > 0)
            .then(|| match_component(chars, end + spaces))
            .flatten()
        else {
            return false;
        };
        end = next;
    }
}

/// `_PATH_RE`: one or more directory segments plus a final component, which is kept.
fn match_path(chars: &[char], index: usize) -> Option<(usize, String)> {
    let after_start = match_start(chars, index)?;
    let mut segment_ends = Vec::new();
    let mut cursor = after_start;
    while let Some(end) = match_spaced_component(chars, cursor) {
        if !chars.get(end).is_some_and(|ch| is_separator(*ch)) {
            break;
        }
        cursor = end + 1;
        segment_ends.push(cursor);
    }
    // Greedy with backtracking, like the regex: the longest prefix that still
    // leaves a final component wins.
    segment_ends.iter().rev().find_map(|&start| {
        let end = match_component(chars, start)?;
        let basename: String = chars[start..end].iter().collect();
        Some((end, format!("{FILTERED}/{basename}")))
    })
}
