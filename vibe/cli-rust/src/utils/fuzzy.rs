//! Fuzzy scoring kept in sync with the Textual path completer.

pub fn score(pattern: &str, text: &str) -> Option<i64> {
    if pattern.is_empty() {
        return Some(0);
    }

    let pattern_original = pattern.chars().collect::<Vec<_>>();
    let pattern_lower = pattern.to_lowercase().chars().collect::<Vec<_>>();
    let text_original = text.chars().collect::<Vec<_>>();
    let text_lower = text.to_lowercase().chars().collect::<Vec<_>>();
    if pattern_lower.len() > text_lower.len() {
        return None;
    }

    if text_lower.starts_with(&pattern_lower) {
        let indices = (0..pattern_lower.len()).collect::<Vec<_>>();
        return Some(
            calculate_score(&pattern_original, &text_original, &text_lower, &indices) * 20,
        );
    }

    let word_boundary =
        word_boundary_match(&pattern_lower, &text_lower, &text_original).map(|indices| {
            calculate_score(&pattern_original, &text_original, &text_lower, &indices) * 18
        });
    let consecutive = consecutive_match(&pattern_lower, &text_lower).map(|indices| {
        calculate_score(&pattern_original, &text_original, &text_lower, &indices) * 13
    });
    let subsequence = subsequence_match(&pattern_lower, &text_lower).map(|indices| {
        calculate_score(&pattern_original, &text_original, &text_lower, &indices) * 10
    });
    [word_boundary, consecutive, subsequence]
        .into_iter()
        .flatten()
        .max()
}

fn word_boundary_match(pattern: &[char], text: &[char], original: &[char]) -> Option<Vec<usize>> {
    let mut indices = Vec::with_capacity(pattern.len());
    let mut pattern_index = 0;
    for (index, &character) in text.iter().enumerate() {
        if pattern_index >= pattern.len() {
            break;
        }
        let is_boundary = index == 0
            || matches!(text[index - 1], '/' | '-' | '_' | '.')
            || original.get(index).is_some_and(|character| {
                character.is_uppercase()
                    && original
                        .get(index - 1)
                        .is_some_and(|previous| !previous.is_uppercase())
            });
        if character == pattern[pattern_index]
            && (is_boundary || indices.last() == Some(&(index - 1)) || indices.is_empty())
        {
            indices.push(index);
            pattern_index += 1;
        }
    }
    (pattern_index == pattern.len()).then_some(indices)
}

fn consecutive_match(pattern: &[char], text: &[char]) -> Option<Vec<usize>> {
    let mut indices = Vec::with_capacity(pattern.len());
    let mut pattern_index = 0;
    for (index, &character) in text.iter().enumerate() {
        if pattern_index >= pattern.len() {
            break;
        }
        if character == pattern[pattern_index] {
            indices.push(index);
            pattern_index += 1;
        } else if !indices.is_empty() {
            indices.clear();
            pattern_index = 0;
        }
    }
    (pattern_index == pattern.len()).then_some(indices)
}

fn subsequence_match(pattern: &[char], text: &[char]) -> Option<Vec<usize>> {
    let mut indices = Vec::with_capacity(pattern.len());
    let mut pattern_index = 0;
    for (index, &character) in text.iter().enumerate() {
        if pattern_index >= pattern.len() {
            break;
        }
        if character == pattern[pattern_index] {
            indices.push(index);
            pattern_index += 1;
        }
    }
    (pattern_index == pattern.len()).then_some(indices)
}

fn calculate_score(
    pattern_original: &[char],
    text_original: &[char],
    text_lower: &[char],
    indices: &[usize],
) -> i64 {
    let Some(&first) = indices.first() else {
        return 0;
    };
    let mut score = if first == 0 {
        300
    } else {
        200 - first as i64 * 4
    };
    for pair in indices.windows(2) {
        if pair[1] == pair[0] + 1 {
            score += 20;
        } else {
            score -= (pair[1] - pair[0] - 1) as i64 * 3;
        }
    }
    for (pattern_index, &text_index) in indices.iter().enumerate() {
        if text_index == 0 || matches!(text_lower[text_index - 1], '/' | '-' | '_' | '.') {
            score += 10;
        } else if text_original.get(text_index).is_some_and(|character| {
            character.is_uppercase()
                && text_original
                    .get(text_index - 1)
                    .is_some_and(|previous| !previous.is_uppercase())
        }) {
            score += 6;
        }
        if pattern_original
            .get(pattern_index)
            .zip(text_original.get(text_index))
            .is_some_and(|(pattern, text)| pattern == text)
        {
            score += 4;
        }
    }
    score.max(0)
}
