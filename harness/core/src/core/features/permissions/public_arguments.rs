use std::io;

use serde::Serialize as _;
use serde_json::{
    Value,
    ser::{Formatter, Serializer},
};

use crate::core::tools::external::{ExternalTool, ExternalToolCall};

/// Tool arguments as exposed to permission rules.
///
/// Filter text uses recursively sorted object keys so matching is
/// deterministic. Each argument pattern is matched against two spellings of
/// that JSON: one with a space after each `:` and `,`, and one with no spaces.
/// A pattern therefore does not depend on which of the two spellings its
/// author used.
pub(crate) struct PublicArguments<'call> {
    value: &'call Value,
}

impl<'call> PublicArguments<'call> {
    pub(crate) fn from_call(call: &'call ExternalToolCall) -> Self {
        let value = match &call.call {
            ExternalTool::RuntimeBuiltin { arguments, .. }
            | ExternalTool::Provided { arguments, .. } => arguments,
        };

        Self { value }
    }

    pub(super) fn to_filter_texts(&self) -> FilterTexts {
        let mut value = self.value.to_owned();
        value.sort_all_objects();

        let mut spaced = Vec::new();
        value
            .serialize(&mut Serializer::with_formatter(
                &mut spaced,
                FilterTextFormatter,
            ))
            .expect("serializing JSON arguments cannot fail");
        let compact =
            serde_json::to_string(&value).expect("serializing JSON arguments cannot fail");

        FilterTexts {
            spaced: String::from_utf8(spaced).expect("JSON serialization produces UTF-8"),
            compact,
        }
    }

    pub(super) fn to_canonical_json(&self) -> Vec<u8> {
        let mut value = self.value.to_owned();
        value.sort_all_objects();
        serde_json::to_vec(&value).expect("serializing JSON arguments cannot fail")
    }

    pub(crate) fn to_value(&self) -> Value {
        self.value.clone()
    }
}

/// The two JSON spellings of one call's arguments that argument patterns match.
pub(super) struct FilterTexts {
    /// `{"a": {"c": 3, "d": 2}, "z": 1}`
    pub(super) spaced: String,
    /// `{"a":{"c":3,"d":2},"z":1}`
    pub(super) compact: String,
}

struct FilterTextFormatter;

impl Formatter for FilterTextFormatter {
    fn begin_array_value<W>(&mut self, writer: &mut W, first: bool) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        if !first {
            writer.write_all(b", ")?;
        }
        Ok(())
    }

    fn begin_object_key<W>(&mut self, writer: &mut W, first: bool) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        self.begin_array_value(writer, first)
    }

    fn begin_object_value<W>(&mut self, writer: &mut W) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        writer.write_all(b": ")
    }
}

/// Whether an argument pattern mixes the two spellings of the filter texts, so
/// that it can match neither.
///
/// The check follows what [`PublicArguments::to_filter_texts`] writes. Outside
/// strings, the spaced text has exactly one space after each `:` and `,` and
/// no other whitespace, and the compact text has no whitespace. Every `"`
/// inside a string is escaped, so an unescaped `"` always opens or closes one.
/// Literals outside strings are numbers, `true`, `false` and `null`.
///
/// Each literal run of the pattern, between glob wildcards, is checked on its
/// own, because a wildcard can stand for any text. A run can start inside or
/// outside a string. A pattern is reported only when its literal text fits
/// JSON with either separator spelling but fits neither spelling alone. Other
/// patterns that can match no filter text are left to the caller.
pub(super) fn mixes_separator_spellings(pattern: &str) -> bool {
    let runs = literal_runs(pattern);
    let fits = |spelling| runs.iter().all(|run| run_fits(run, spelling));
    fits(Spelling::Either) && !fits(Spelling::Spaced) && !fits(Spelling::Compact)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Spelling {
    Spaced,
    Compact,
    /// One space or none after each separator.
    Either,
}

#[derive(Clone, Copy)]
enum Position {
    Outside,
    Inside,
    AfterBackslash,
}

/// Splits a valid glob pattern into the runs of characters it matches
/// literally. `*`, `?` and a character class end a run, except that a class
/// of one character, such as `[*]`, is that character.
fn literal_runs(pattern: &str) -> Vec<Vec<char>> {
    let chars: Vec<char> = pattern.chars().collect();
    let mut runs = vec![Vec::new()];
    let mut index = 0;
    while index < chars.len() {
        match chars[index] {
            '*' | '?' => {
                runs.push(Vec::new());
                index += 1;
            }
            '[' => {
                let (members, next) = character_class(&chars, index);
                match members {
                    Some(literal) => runs.last_mut().expect("runs is never empty").push(literal),
                    None => runs.push(Vec::new()),
                }
                index = next;
            }
            literal => {
                runs.last_mut().expect("runs is never empty").push(literal);
                index += 1;
            }
        }
    }
    runs
}

/// Reads the class that opens at `start`, with the `glob` crate's rules: a
/// `]` right after `[` or `[!` is a member. Returns the class's only
/// character when it matches exactly one, and the index after the class.
fn character_class(chars: &[char], start: usize) -> (Option<char>, usize) {
    let negated = chars.get(start + 1) == Some(&'!');
    let first = start + 1 + usize::from(negated);
    let Some(close) = chars
        .iter()
        .skip(first + 1)
        .position(|&char| char == ']')
        .map(|offset| first + 1 + offset)
    else {
        // The pattern is a valid glob, so this is unreachable; end the run.
        return (None, chars.len());
    };
    let literal = (!negated && close == first + 1).then(|| chars[first]);
    (literal, close + 1)
}

fn run_fits(run: &[char], spelling: Spelling) -> bool {
    [
        Position::Outside,
        Position::Inside,
        Position::AfterBackslash,
    ]
    .into_iter()
    .any(|start| run_fits_from(run, spelling, start))
}

fn run_fits_from(run: &[char], spelling: Spelling, start: Position) -> bool {
    let mut state = start;
    // The text before a run is unknown, so it may end with a separator.
    let mut after_separator = true;
    // Letters seen so far in the current `Position::Outside` run, such as the
    // `tat` of `state`. JSON allows bare letters outside a string only as part
    // of `true`, `false` or `null` (or the exponent `e`), so this is checked,
    // and not just each letter on its own, once the run of letters ends.
    // Otherwise a key or string value built only from those letters, such as
    // `state` or `status`, reads as a bare literal and hides a mismatched
    // separator next to it.
    let mut outside_literal = String::new();
    for (index, &char) in run.iter().enumerate() {
        let follows_separator = std::mem::replace(&mut after_separator, false);
        let continues_outside_literal =
            matches!(state, Position::Outside) && is_outside_literal_letter(char);
        if !continues_outside_literal
            && !outside_literal.is_empty()
            && !is_substring_of_json_literal(&outside_literal)
        {
            return false;
        }
        if !continues_outside_literal {
            outside_literal.clear();
        }
        match state {
            Position::AfterBackslash => state = Position::Inside,
            Position::Inside => match char {
                '\\' => state = Position::AfterBackslash,
                '"' => state = Position::Outside,
                _ => {}
            },
            Position::Outside => match char {
                '"' => state = Position::Inside,
                ':' | ',' => {
                    let next = run.get(index + 1);
                    if spelling == Spelling::Spaced && next.is_some_and(|&next| next != ' ') {
                        return false;
                    }
                    after_separator = true;
                }
                ' ' if spelling == Spelling::Compact || !follows_separator => return false,
                ' ' | '{' | '}' | '[' | ']' | '0'..='9' | '-' | '+' | '.' | 'E' => {}
                letter if is_outside_literal_letter(letter) => outside_literal.push(letter),
                _ => return false,
            },
        }
    }
    outside_literal.is_empty() || is_substring_of_json_literal(&outside_literal)
}

/// Whether `char` is one of the letters of `true`, `false`, `null` or the
/// exponent `e`, the only bare letters JSON allows outside a string.
fn is_outside_literal_letter(char: char) -> bool {
    matches!(char, 'a' | 'e' | 'f' | 'l' | 'n' | 'r' | 's' | 't' | 'u')
}

/// Whether a run of bare letters outside a string could be part of `true`,
/// `false` or `null`, including a run cut short by a glob wildcard.
fn is_substring_of_json_literal(letters: &str) -> bool {
    ["true", "false", "null"]
        .into_iter()
        .any(|literal| literal.contains(letters))
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn to_filter_texts_sorts_object_keys_and_uses_both_separator_spellings() {
        let value = json!({"z": 1, "a": {"d": 2, "c": 3}});
        let texts = PublicArguments { value: &value }.to_filter_texts();

        assert_eq!(texts.spaced, r#"{"a": {"c": 3, "d": 2}, "z": 1}"#);
        assert_eq!(texts.compact, r#"{"a":{"c":3,"d":2},"z":1}"#);
    }

    #[test]
    fn to_filter_texts_serializes_arrays_and_scalar_values() {
        let value = json!([null, true, false, "line\n\"quoted\", a: b", [2, 1]]);
        let texts = PublicArguments { value: &value }.to_filter_texts();

        assert_eq!(
            texts.spaced,
            r#"[null, true, false, "line\n\"quoted\", a: b", [2, 1]]"#
        );
        assert_eq!(
            texts.compact,
            r#"[null,true,false,"line\n\"quoted\", a: b",[2,1]]"#
        );
    }

    #[rstest::rstest]
    #[case::spaced_after_compact(r#"*"environment":"production", "region": "eu"*"#)]
    #[case::after_a_key_ending_in_backslash(r#"*"dir\\":"production", "region": "eu"*"#)]
    #[case::after_a_number(r#"*"retries": 3,"region": "eu"*"#)]
    #[case::between_array_numbers(r#"*"ports": [[]80,443]*"#)]
    #[case::after_true(r#"*"dry_run": true,"region": "eu"*"#)]
    #[case::across_a_one_character_wildcard(r#"*"a": "b"?"c":"d"*"#)]
    #[case::one_spelling_in_each_run(r#"*"a": "b"*"c":"d"*"#)]
    #[case::keys_spelled_with_only_json_literal_letters(r#"*"state": false,"status": true*"#)]
    #[case::keys_spelled_with_only_json_literal_letters_other_order(
        r#"*"status": true,"state": false*"#
    )]
    #[case::strings_that_start_and_end_with_a_separator(r#"*", state, ":", tate, ", ", sat, "*"#)]
    fn mixes_separator_spellings_reports_a_pattern_that_fits_neither_spelling(
        #[case] pattern: &str,
    ) {
        assert!(mixes_separator_spellings(pattern), "{pattern}");
    }

    #[rstest::rstest]
    #[case::spaced(r#"*"value": "production"*"#)]
    #[case::compact(r#"*"value":"production"*"#)]
    #[case::value_only("*production*")]
    #[case::separator_then_wildcard(r#"*"value":*"#)]
    #[case::escaped_quote_before_colon(r#"*"note": "say \":hi"*"#)]
    #[case::escaped_backslash_then_escaped_quote(r#"*"note": "say \\\":hi"*"#)]
    #[case::spaced_after_a_number(r#"*"retries": 3, "region": "eu"*"#)]
    #[case::compact_after_a_number(r#"*"retries":3,"region":"eu"*"#)]
    #[case::string_that_starts_with_a_comma(r#"*"note": ",a,b: c"*"#)]
    #[case::separators_inside_a_string(r#"*"note": "a,b: c"*"#)]
    #[case::not_json_at_all(r#"*"a"x"b"*"#)]
    fn mixes_separator_spellings_accepts_a_pattern_with_one_spelling(#[case] pattern: &str) {
        assert!(!mixes_separator_spellings(pattern), "{pattern}");
    }

    /// Argument values whose filter texts the generated patterns are cut
    /// from. They cover keys and values spelled only with the letters of
    /// `true`, `false` and `null`, strings that start or end with a separator,
    /// escapes, nesting and every kind of JSON literal.
    fn sample_arguments() -> Vec<Value> {
        vec![
            json!({"environment": "production", "region": "eu", "retries": 3}),
            json!({"note": "say \":hi, then: \\ go", "ports": [80, 443], "dry_run": true}),
            json!([null, -1.5e3, {"a": [], "b": {}}, ",x", "a: b, c"]),
            json!({"state": false, "status": true, "true": null, "null": "false"}),
            json!({"sep": ", ", "colon": ":", "list": ",a,b,", "close": "}", "open": "[{"}),
            json!({"path": "C:\\dir\\", "quote": "\"", "tab": "a\tb", "unicode": "é ü"}),
            json!({"a": {"b": {"c": [1, [2, [3, {"d": "e"}]]]}}, "f": [true, false, null]}),
            json!({"cmd": "rm -rf /", "args": ["-r", "-f", "/tmp/x y"], "env": {}}),
            json!({"x": 0, "y": -0.25, "z": 1e100, "w": 12345678901234567890u64}),
            json!({"": "", " ": " ", ",": ",", ":": ":"}),
        ]
    }

    /// The filter texts that real arguments produce, both spellings.
    fn sample_filter_texts() -> Vec<String> {
        sample_arguments()
            .iter()
            .flat_map(|value| {
                let texts = PublicArguments { value }.to_filter_texts();
                [texts.spaced, texts.compact]
            })
            .collect()
    }

    #[test]
    fn mixes_separator_spellings_accepts_every_part_of_real_filter_texts() {
        let mut checked = 0;
        for text in sample_filter_texts() {
            let chars: Vec<char> = text.chars().collect();
            for start in 0..chars.len() {
                for end in start + 1..=chars.len() {
                    let part: String = chars[start..end].iter().collect();
                    let pattern = format!("*{}*", glob::Pattern::escape(&part));
                    assert!(!mixes_separator_spellings(&pattern), "{pattern}");
                    checked += 1;
                }
            }
        }
        assert!(checked > 10_000, "{checked}");
    }

    /// Cuts real filter texts at random places and puts a wildcard in each
    /// cut, so every generated pattern matches the text it was cut from.
    #[test]
    fn mixes_separator_spellings_accepts_patterns_cut_from_real_filter_texts() {
        // A fixed xorshift generator keeps the test deterministic.
        let mut seed: u64 = 0x9E37_79B9_7F4A_7C15;
        let mut next = |bound: usize| {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            usize::try_from(seed % u64::try_from(bound).unwrap()).unwrap()
        };
        let mut checked = 0;
        for text in sample_filter_texts() {
            let chars: Vec<char> = text.chars().collect();
            for _ in 0..3_000 {
                let mut cuts: Vec<usize> =
                    (0..1 + next(4)).map(|_| next(chars.len() + 1)).collect();
                cuts.sort_unstable();
                cuts.dedup();
                let mut pattern = String::new();
                let mut position = 0;
                for (index, &cut) in cuts.iter().enumerate() {
                    let literal: String = chars[position..cut.max(position)].iter().collect();
                    pattern.push_str(&glob::Pattern::escape(&literal));
                    position = cut.max(position);
                    // `?` stands for exactly one character; `*` for any text.
                    if index % 2 == 1 && position < chars.len() {
                        pattern.push('?');
                        position += 1;
                    } else {
                        // `**` is not a valid glob, so a second `*` in a row
                        // only widens the first one.
                        if !pattern.ends_with('*') {
                            pattern.push('*');
                        }
                        position += next(chars.len() - position + 1);
                    }
                }
                let rest: String = chars[position.min(chars.len())..].iter().collect();
                pattern.push_str(&glob::Pattern::escape(&rest));
                assert!(
                    glob::Pattern::new(&pattern).unwrap().matches(&text),
                    "{pattern} does not match {text}"
                );
                assert!(
                    !mixes_separator_spellings(&pattern),
                    "{pattern} from {text}"
                );
                checked += 1;
            }
        }
        assert!(checked >= 30_000, "{checked}");
    }
}
