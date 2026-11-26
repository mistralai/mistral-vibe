//! Locate the single edit that turned one text into another.

/// The edited region as `(prefix, old_end, new_end)`: `old[prefix..old_end]`
/// became `new[prefix..new_end]`, with the common prefix and suffix untouched,
/// for an edit known to start at byte `at` or later: the common prefix stops
/// there, so the region never slides into text the edit merely repeats.
pub fn changed_range_from(old: &str, new: &str, at: usize) -> (usize, usize, usize) {
    let prefix: usize = old
        .chars()
        .zip(new.chars())
        .take_while(|(a, b)| a == b)
        .map(|(ch, _)| ch.len_utf8())
        .scan(0, |sum, len| {
            *sum += len;
            (*sum <= at).then_some(len)
        })
        .sum();
    let suffix: usize = old[prefix..]
        .chars()
        .rev()
        .zip(new[prefix..].chars().rev())
        .take_while(|(a, b)| a == b)
        .map(|(ch, _)| ch.len_utf8())
        .sum();
    (prefix, old.len() - suffix, new.len() - suffix)
}
