//! Bare URL and email autolinking, matching markdown-it's linkify pass.

/// Characters linkify never keeps at either end of a link.
const LEADING: &[char] = &['(', '[', '{', '"', '\'', '<'];
const TRAILING: &[char] = &['.', ',', ';', ':', '!', '?', ')', ']', '}', '"', '\'', '>'];
/// linkify-it's default gTLD list; every two-letter TLD is accepted on top.
const TLDS: &[&str] = &[
    "biz", "com", "edu", "gov", "net", "org", "pro", "web", "xxx", "aero", "asia", "coop", "info",
    "museum", "name", "shop",
];

/// Split `text` into runs, each with the href it autolinks to (`None` = plain).
pub fn split(text: &str) -> Vec<(&str, Option<String>)> {
    let mut out: Vec<(&str, Option<String>)> = Vec::new();
    let mut plain_from = 0;
    let mut cursor = 0;
    while cursor < text.len() {
        let token_start = cursor + skip(&text[cursor..], char::is_whitespace);
        let token_end = token_start + skip(&text[token_start..], |c| !c.is_whitespace());
        if token_start == token_end {
            break;
        }
        cursor = token_end;
        let token = &text[token_start..token_end];
        let start = token_end - token.trim_start_matches(LEADING).len();
        let candidate = text[start..token_end].trim_end_matches(TRAILING);
        let Some(href) = href_of(candidate) else {
            continue;
        };
        if plain_from < start {
            out.push((&text[plain_from..start], None));
        }
        out.push((candidate, Some(href)));
        plain_from = start + candidate.len();
    }
    if plain_from < text.len() {
        out.push((&text[plain_from..], None));
    }
    out
}

/// Byte length of the leading run of `text` whose chars satisfy `keep`.
fn skip(text: &str, keep: impl Fn(char) -> bool) -> usize {
    text.find(|c| !keep(c)).unwrap_or(text.len())
}

/// The href a bare token autolinks to, or `None` when it is plain text.
fn href_of(candidate: &str) -> Option<String> {
    if candidate.starts_with("http://") || candidate.starts_with("https://") {
        return (candidate.len() > "https://".len()).then(|| candidate.to_owned());
    }
    if let Some((local, domain)) = candidate.split_once('@') {
        let mailbox = !local.is_empty()
            && local
                .chars()
                .all(|c| c.is_alphanumeric() || "._%+-".contains(c))
            && !domain.contains(['@', '/']);
        return (mailbox && is_host(domain)).then(|| format!("mailto:{candidate}"));
    }
    is_host(host_of(candidate)).then(|| format!("http://{candidate}"))
}

/// The authority part of a bare link, before any path, query, or fragment.
fn host_of(candidate: &str) -> &str {
    candidate
        .split_once(['/', '?', '#'])
        .map_or(candidate, |(host, _)| host)
}

/// A dotted host whose last label is a TLD linkify would recognize.
fn is_host(host: &str) -> bool {
    let Some((labels, tld)) = host.rsplit_once('.') else {
        return false;
    };
    let tld = tld.to_ascii_lowercase();
    !labels.is_empty()
        && labels
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
        && tld.chars().all(|c| c.is_ascii_alphabetic())
        && (tld.len() == 2 || TLDS.contains(&tld.as_str()))
}
