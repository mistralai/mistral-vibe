//! Search in a task's output: every match of the query, and the one the view jumped to, as in a pager.

use crate::tasks_panel::Output;

#[cfg(test)]
mod tests;

/// A match: its line, numbered from the task's first line, and the byte it starts at, so trims and rewraps never move it.
pub type Hit = (usize, usize);

#[derive(Default)]
pub struct Find {
    /// The search, lowercased; empty when there is none.
    query: String,
    /// The match the view jumped to.
    pub current: Option<Hit>,
    /// Every match in the output, in order.
    hits: Vec<Hit>,
}

impl Find {
    pub fn query(&self) -> &str {
        &self.query
    }

    pub fn hits(&self) -> &[Hit] {
        &self.hits
    }

    /// Search `output` for `query`, ignoring case; false when that changes nothing.
    pub fn set_query(&mut self, query: &str, output: &Output) -> bool {
        let query = fold(query);
        let query = match query.trim().is_empty() {
            true => String::new(),
            false => query,
        };
        if query == self.query {
            return false;
        }
        self.hits = find(output.lines(), output.first_line(), &query);
        self.query = query;
        self.current = None;
        true
    }

    /// Follow a page that dropped lines above and wrote lines from `from` on.
    pub fn appended(&mut self, output: &Output, from: usize) {
        let kept = output.first_line()..from;
        self.hits.retain(|(line, _)| kept.contains(line));
        let lines = output.lines();
        let fresh = lines.get(from - output.first_line()..).unwrap_or_default();
        self.hits.extend(find(fresh, from, &self.query));
    }

    /// Move to the match after (`forward`) or before the current one, wrapping; with none current, the last one.
    pub fn step(&mut self, forward: bool) -> Option<Hit> {
        let hits = &self.hits;
        let next = match (self.current, forward) {
            (None, _) => hits.last(),
            (Some(at), true) => hits.iter().find(|hit| **hit > at).or(hits.first()),
            (Some(at), false) => hits.iter().rev().find(|hit| **hit < at).or(hits.last()),
        };
        self.current = next.copied();
        self.current
    }

    /// The current match's place among all of them, if it is still one, and how many there are.
    pub fn position(&self) -> (Option<usize>, usize) {
        let index = (self.current).and_then(|current| self.hits.binary_search(&current).ok());
        (index.map(|index| index + 1), self.hits.len())
    }
}

/// `text` lowercased where that keeps each character's byte length, so a match maps back onto the original byte for byte.
pub fn fold(text: &str) -> String {
    text.chars()
        .map(|c| {
            let mut lower = c.to_lowercase();
            match (lower.next(), lower.next()) {
                (Some(l), None) if l.len_utf8() == c.len_utf8() => l,
                _ => c,
            }
        })
        .collect()
}

/// Where `query` starts in `lines`, the first numbered `first`, ignoring case.
pub fn find(lines: &[String], first: usize, query: &str) -> Vec<Hit> {
    if query.is_empty() {
        return Vec::new();
    }
    let mut hits = Vec::new();
    for (index, line) in lines.iter().enumerate() {
        let lower = fold(line);
        let starts = lower.match_indices(query).map(|(start, _)| start);
        hits.extend(
            starts
                .filter(|start| {
                    line.is_char_boundary(*start) && line.is_char_boundary(start + query.len())
                })
                .map(|start| (first + index, start)),
        );
    }
    hits
}
