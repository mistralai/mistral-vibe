//! Port of the Python `difflib` subset `render_edit_diff` relies on.

use std::collections::HashMap;

/// Python `SequenceMatcher.get_opcodes` tags.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Tag {
    Equal,
    Replace,
    Delete,
    Insert,
}

#[derive(Clone, Copy)]
pub struct Opcode {
    pub tag: Tag,
    pub i1: usize,
    pub i2: usize,
    pub j1: usize,
    pub j2: usize,
}

/// One `@@` hunk: the line numbers its header would carry, and its `-`/`+`/` ` rows.
pub struct Hunk {
    pub old_start: usize,
    pub new_start: usize,
    pub rows: Vec<(char, String)>,
}

/// Python `difflib.unified_diff(a, b, n=n)`, minus the file headers it emits.
pub fn unified_diff(a: &[&str], b: &[&str], n: usize) -> Vec<Hunk> {
    grouped_opcodes(a, b, n)
        .into_iter()
        .map(|group| hunk(a, b, &group))
        .collect()
}

fn hunk(a: &[&str], b: &[&str], group: &[Opcode]) -> Hunk {
    let (first, last) = (group[0], group[group.len() - 1]);
    let mut rows = Vec::new();
    for code in group {
        match code.tag {
            Tag::Equal => rows.extend(a[code.i1..code.i2].iter().map(|l| (' ', (*l).to_owned()))),
            Tag::Delete => rows.extend(a[code.i1..code.i2].iter().map(|l| ('-', (*l).to_owned()))),
            Tag::Insert => rows.extend(b[code.j1..code.j2].iter().map(|l| ('+', (*l).to_owned()))),
            Tag::Replace => {
                rows.extend(a[code.i1..code.i2].iter().map(|l| ('-', (*l).to_owned())));
                rows.extend(b[code.j1..code.j2].iter().map(|l| ('+', (*l).to_owned())));
            }
        }
    }
    Hunk {
        old_start: range_start(first.i1, last.i2),
        new_start: range_start(first.j1, last.j2),
        rows,
    }
}

/// Python `_format_range_unified`: 1-based, but an empty range starts one line earlier.
fn range_start(start: usize, stop: usize) -> usize {
    if stop == start {
        start
    } else {
        start + 1
    }
}

/// Python `SequenceMatcher.get_grouped_opcodes`: opcodes split into hunks with `n` context lines.
fn grouped_opcodes(a: &[&str], b: &[&str], n: usize) -> Vec<Vec<Opcode>> {
    let mut codes = get_opcodes(a, b);
    if codes.is_empty() {
        codes.push(Opcode {
            tag: Tag::Equal,
            i1: 0,
            i2: 1,
            j1: 0,
            j2: 1,
        });
    }
    if let Some(first) = codes.first_mut() {
        if first.tag == Tag::Equal {
            first.i1 = first.i1.max(first.i2.saturating_sub(n));
            first.j1 = first.j1.max(first.j2.saturating_sub(n));
        }
    }
    if let Some(last) = codes.last_mut() {
        if last.tag == Tag::Equal {
            last.i2 = last.i2.min(last.i1 + n);
            last.j2 = last.j2.min(last.j1 + n);
        }
    }
    let mut groups = Vec::new();
    let mut group: Vec<Opcode> = Vec::new();
    for mut code in codes {
        if code.tag == Tag::Equal && code.i2 - code.i1 > n + n {
            group.push(Opcode {
                i2: code.i2.min(code.i1 + n),
                j2: code.j2.min(code.j1 + n),
                ..code
            });
            groups.push(std::mem::take(&mut group));
            code.i1 = code.i1.max(code.i2.saturating_sub(n));
            code.j1 = code.j1.max(code.j2.saturating_sub(n));
        }
        group.push(code);
    }
    if !(group.is_empty() || (group.len() == 1 && group[0].tag == Tag::Equal)) {
        groups.push(group);
    }
    groups
}

/// Python `SequenceMatcher.get_opcodes`.
fn get_opcodes(a: &[&str], b: &[&str]) -> Vec<Opcode> {
    let (mut i, mut j) = (0, 0);
    let mut codes = Vec::new();
    for (ai, bj, size) in matching_blocks(a, b) {
        let tag = match (ai > i, bj > j) {
            (true, true) => Some(Tag::Replace),
            (true, false) => Some(Tag::Delete),
            (false, true) => Some(Tag::Insert),
            (false, false) => None,
        };
        if let Some(tag) = tag {
            codes.push(Opcode {
                tag,
                i1: i,
                i2: ai,
                j1: j,
                j2: bj,
            });
        }
        i = ai + size;
        j = bj + size;
        if size > 0 {
            codes.push(Opcode {
                tag: Tag::Equal,
                i1: ai,
                i2: i,
                j1: bj,
                j2: j,
            });
        }
    }
    codes
}

/// Python `SequenceMatcher.get_matching_blocks`, sentinel included: the empty
/// block at the end is what closes a trailing insert or replace.
fn matching_blocks(a: &[&str], b: &[&str]) -> Vec<(usize, usize, usize)> {
    let b2j = build_b2j(b);
    let mut queue = vec![(0, a.len(), 0, b.len())];
    let mut blocks = Vec::new();
    while let Some((a_lo, a_hi, b_lo, b_hi)) = queue.pop() {
        let (i, j, k) = find_longest_match(a, b, &b2j, a_lo, a_hi, b_lo, b_hi);
        if k == 0 {
            continue;
        }
        blocks.push((i, j, k));
        if a_lo < i && b_lo < j {
            queue.push((a_lo, i, b_lo, j));
        }
        if i + k < a_hi && j + k < b_hi {
            queue.push((i + k, a_hi, j + k, b_hi));
        }
    }
    blocks.sort_unstable();
    let mut blocks = merge_adjacent(blocks);
    blocks.push((a.len(), b.len(), 0));
    blocks
}

fn merge_adjacent(blocks: Vec<(usize, usize, usize)>) -> Vec<(usize, usize, usize)> {
    let mut merged: Vec<(usize, usize, usize)> = Vec::with_capacity(blocks.len());
    for (i, j, k) in blocks {
        match merged.last_mut() {
            Some(last) if last.0 + last.2 == i && last.1 + last.2 == j => last.2 += k,
            _ => merged.push((i, j, k)),
        }
    }
    merged
}

/// Python's `b2j`, including the `autojunk` heuristic that drops popular lines.
fn build_b2j<'a>(b: &[&'a str]) -> HashMap<&'a str, Vec<usize>> {
    let mut b2j: HashMap<&str, Vec<usize>> = HashMap::new();
    for (j, line) in b.iter().enumerate() {
        b2j.entry(line).or_default().push(j);
    }
    if b.len() >= 200 {
        let limit = b.len() / 100 + 1;
        b2j.retain(|_, indices| indices.len() <= limit);
    }
    b2j
}

/// Python `find_longest_match` with `isjunk=None`, so the junk-extension passes are no-ops.
fn find_longest_match(
    a: &[&str],
    b: &[&str],
    b2j: &HashMap<&str, Vec<usize>>,
    a_lo: usize,
    a_hi: usize,
    b_lo: usize,
    b_hi: usize,
) -> (usize, usize, usize) {
    let (mut besti, mut bestj, mut bestsize) = (a_lo, b_lo, 0usize);
    let mut j2len: HashMap<usize, usize> = HashMap::new();
    for (i, line) in a.iter().enumerate().take(a_hi).skip(a_lo) {
        let mut newj2len: HashMap<usize, usize> = HashMap::new();
        for &j in b2j.get(line).map(Vec::as_slice).unwrap_or_default() {
            if j < b_lo {
                continue;
            }
            if j >= b_hi {
                break;
            }
            let k = j.checked_sub(1).and_then(|p| j2len.get(&p)).unwrap_or(&0) + 1;
            newj2len.insert(j, k);
            if k > bestsize {
                (besti, bestj, bestsize) = (i + 1 - k, j + 1 - k, k);
            }
        }
        j2len = newj2len;
    }
    while besti > a_lo && bestj > b_lo && a[besti - 1] == b[bestj - 1] {
        (besti, bestj, bestsize) = (besti - 1, bestj - 1, bestsize + 1);
    }
    while besti + bestsize < a_hi
        && bestj + bestsize < b_hi
        && a[besti + bestsize] == b[bestj + bestsize]
    {
        bestsize += 1;
    }
    (besti, bestj, bestsize)
}
