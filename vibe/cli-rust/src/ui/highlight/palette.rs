//! TextMate scopes to Textual's `HighlightTheme` token styles.

use ratatui::style::{Color, Modifier, Style};
use syntect::parsing::Scope;

use crate::ui::theme;

/// The pygments token types Textual's highlight themes give a style to.
#[derive(Clone, Copy, PartialEq)]
pub(super) enum Tok {
    Comment,
    Keyword,
    KeywordConstant,
    KeywordNamespace,
    KeywordType,
    Number,
    Str,
    StrBacktick,
    StrDoc,
    StrDouble,
    Name,
    NameAttribute,
    NameBuiltin,
    NameBuiltinPseudo,
    NameClass,
    NameConstant,
    NameDecorator,
    NameFunction,
    NameTag,
    NameVariable,
    Operator,
    OperatorWord,
}

/// The primitive types pygments' rust lexer tags `Keyword.Type`.
const RUST_PRIMITIVES: &[&str] = &[
    "bool", "char", "str", "isize", "usize", "f32", "f64", "i8", "i16", "i32", "i64", "i128", "u8",
    "u16", "u32", "u64", "u128",
];

/// Scope prefixes, longest first: the TextMate equivalent of a pygments token.
const SCOPES: &[(&str, Tok)] = &[
    ("comment", Tok::Comment),
    ("constant.language", Tok::KeywordConstant),
    ("constant.numeric", Tok::Number),
    ("constant.other", Tok::NameConstant),
    ("entity.name.class", Tok::NameClass),
    ("entity.name.function", Tok::NameFunction),
    ("entity.name.tag", Tok::NameTag),
    ("entity.name.type", Tok::NameClass),
    ("entity.name", Tok::Name),
    ("entity.other.attribute-name", Tok::NameAttribute),
    ("keyword.control.import", Tok::KeywordNamespace),
    ("keyword.operator.word", Tok::OperatorWord),
    ("keyword.operator", Tok::Operator),
    ("keyword.other.import", Tok::KeywordNamespace),
    ("keyword", Tok::Keyword),
    // Sigils belong to the token they introduce: `$` to the variable, `@` to
    // the decorator, as pygments lexes them.
    ("punctuation.definition.annotation", Tok::NameDecorator),
    ("punctuation.definition.variable", Tok::NameVariable),
    ("storage.modifier", Tok::Keyword),
    // A string prefix (`f'...'`) is pygments' String.Affix, which has no style.
    ("storage.type.string", Tok::Name),
    // `=>` is scoped as a storage type but pygments calls it punctuation.
    ("storage.type.function.arrow", Tok::Operator),
    // Declaration keywords (`func`, `const`, `class`) are scoped as storage
    // types too; real type names reach us as `support.type` instead.
    ("storage.type", Tok::Keyword),
    // Python's grammar scopes a docstring as a block comment, but pygments
    // tags it `Literal.String.Doc`.
    ("comment.block.documentation", Tok::StrDoc),
    // Rust's grammar suffixes its scopes with `.rust`; pygments' rust lexer
    // emits plain `Literal.String` for its strings, which Textual's ANSI theme
    // maps to the misspelled `ansi_greenb` (unstyled), and treats format
    // placeholders as string content, not constants.
    ("string.quoted.double.rust", Tok::Str),
    ("string.quoted.double", Tok::StrDouble),
    ("string.quoted.other.backtick", Tok::StrBacktick),
    ("string.quoted.docstring", Tok::StrDoc),
    ("string", Tok::Str),
    ("constant.other.placeholder", Tok::Str),
    ("support.class", Tok::NameClass),
    ("support.function", Tok::NameBuiltin),
    ("support.type", Tok::NameBuiltin),
    ("variable.annotation", Tok::NameDecorator),
    ("variable.function", Tok::NameFunction),
    ("variable.language", Tok::NameBuiltinPseudo),
    ("variable.parameter", Tok::NameVariable),
    ("variable", Tok::NameVariable),
];

/// The innermost scope that maps to a token, dropping dotted segments as
/// pygments walks a token type up to its parent.
pub(super) fn token(scopes: &[Scope], text: &str) -> Option<Tok> {
    let scope_names = scopes
        .iter()
        .map(|scope| scope.build_string())
        .collect::<Vec<_>>();
    if scope_names.iter().any(|scope| scope == "source.python")
        && scope_names
            .iter()
            .any(|scope| scope.starts_with("comment.") && scope.contains("documentation"))
    {
        return Some(Tok::StrDoc);
    }
    let rust = scope_names.iter().any(|scope| scope == "source.rust");
    if rust {
        if text == "&" {
            return Some(Tok::Keyword);
        }
        if matches!(
            text,
            "bool"
                | "char"
                | "str"
                | "i8"
                | "i16"
                | "i32"
                | "i64"
                | "i128"
                | "isize"
                | "u8"
                | "u16"
                | "u32"
                | "u64"
                | "u128"
                | "usize"
                | "f32"
                | "f64"
        ) {
            return Some(Tok::NameBuiltin);
        }
        // Pygments leaves Rust string contents unstyled in Textual's ANSI
        // palette, including interpolation punctuation.
        if scope_names.iter().any(|scope| scope.starts_with("string.")) {
            return Some(Tok::Name);
        }
    }
    if scope_names
        .iter()
        .any(|scope| scope.starts_with("string.quoted.docstring"))
    {
        return Some(Tok::StrDoc);
    }
    // Grammars scope `self` as an ordinary parameter; pygments calls it a pseudo-builtin.
    if matches!(text, "self" | "cls") {
        return Some(Tok::NameBuiltinPseudo);
    }
    // A mapping key is a string to the grammar, a tag to pygments.
    if scopes
        .iter()
        .any(|s| s.build_string().starts_with("meta.mapping.key"))
    {
        return Some(Tok::NameTag);
    }
    // Rust's grammar and pygments' rust lexer disagree on two shapes: the
    // reference sigil `&` is a pseudo keyword (Keyword's bold magenta), and
    // primitive types are `Keyword.Type`, though the grammar scopes them as an
    // ordinary operator and a storage type.
    if scopes.iter().any(|s| s.build_string() == "source.rust") {
        if text == "&" {
            return Some(Tok::Keyword);
        }
        if RUST_PRIMITIVES.contains(&text)
            && scopes
                .iter()
                .any(|s| s.build_string().starts_with("storage.type"))
        {
            return Some(Tok::KeywordType);
        }
    }
    let mut generic_name = false;
    for name in scope_names.iter().rev() {
        // `meta.*` describes a region, not a token; only its plain-identifier
        // marker matters, and only if nothing more specific claims the text.
        if let Some(rest) = name.strip_prefix("meta.") {
            generic_name |= rest.starts_with("generic-name");
            continue;
        }
        let mut candidate = name.as_str();
        loop {
            if let Some((_, tok)) = SCOPES.iter().find(|(prefix, _)| *prefix == candidate) {
                return Some(*tok);
            }
            match candidate.rfind('.') {
                Some(dot) => candidate = &candidate[..dot],
                None => break,
            }
        }
    }
    // A bare identifier: pygments' Token.Name.
    generic_name.then_some(Tok::Name)
}

/// Textual paints unstyled fence text with `$text`.
pub(super) fn plain() -> Style {
    Style::default().fg(theme::code_plain())
}

pub(super) fn style(tok: Tok) -> Style {
    if theme::is_ansi() {
        ansi(tok)
    } else {
        truecolor(tok)
    }
}

/// `ANSIDarkHighlightTheme` / `ANSILightHighlightTheme`. Entries whose color
/// name is misspelled upstream (`ansi_greenb`, `ansi_yelllow`, `ansi_blow`)
/// fail to parse in Textual and leave the text unstyled, so they map to `Name`.
fn ansi(tok: Tok) -> Style {
    let light = !theme::is_dark();
    match tok {
        Tok::Comment => Style::default().add_modifier(Modifier::DIM | Modifier::ITALIC),
        Tok::NameBuiltinPseudo => plain().add_modifier(Modifier::ITALIC),
        Tok::Keyword => fg(Color::Magenta).add_modifier(Modifier::BOLD),
        Tok::KeywordConstant | Tok::NameBuiltin => fg(Color::Cyan),
        Tok::KeywordNamespace | Tok::OperatorWord => fg(Color::Magenta),
        Tok::KeywordType => fg(Color::Cyan),
        Tok::Number if light => fg(Color::Blue).add_modifier(Modifier::BOLD),
        Tok::Number => fg(Color::Yellow),
        Tok::StrBacktick => fg(Color::DarkGray),
        Tok::StrDouble => fg(Color::Green),
        Tok::StrDoc => fg(Color::Green).add_modifier(Modifier::ITALIC),
        Tok::NameClass if light => fg(Color::Blue).add_modifier(Modifier::BOLD),
        Tok::NameClass => fg(Color::Yellow),
        Tok::NameConstant => fg(Color::Red),
        Tok::NameDecorator | Tok::NameTag => fg(Color::Blue).add_modifier(Modifier::BOLD),
        Tok::NameFunction => fg(Color::Blue),
        Tok::Str | Tok::Name | Tok::NameAttribute | Tok::NameVariable | Tok::Operator => plain(),
    }
}

/// `HighlightTheme`, whose `$text-*` variables Textual tints from the theme.
fn truecolor(tok: Tok) -> Style {
    match tok {
        Tok::Comment => fg(theme::blend(
            theme::background(),
            theme::auto_contrast(),
            0.6,
        )),
        Tok::Keyword | Tok::NameBuiltin => fg(theme::text_accent()),
        Tok::NameBuiltinPseudo => plain().add_modifier(Modifier::ITALIC),
        Tok::KeywordConstant => fg(theme::text_success()).add_modifier(Modifier::BOLD),
        Tok::KeywordNamespace | Tok::NameConstant => fg(theme::text_error()),
        // `Token.Keyword.Type` is bare `bold` in the base theme.
        Tok::KeywordType => plain().add_modifier(Modifier::BOLD),
        Tok::Number | Tok::NameAttribute => fg(theme::text_warning()),
        Tok::StrBacktick => fg(theme::blend(
            theme::background(),
            theme::auto_contrast(),
            0.6,
        )),
        Tok::Str | Tok::StrDouble => fg(theme::text_success()),
        Tok::StrDoc => fg(theme::text_success()).add_modifier(Modifier::ITALIC),
        Tok::Name => fg(theme::text_primary()),
        Tok::NameClass => fg(theme::text_warning()).add_modifier(Modifier::BOLD),
        Tok::NameDecorator | Tok::NameTag => fg(theme::text_primary()).add_modifier(Modifier::BOLD),
        Tok::NameFunction => fg(theme::text_warning()).add_modifier(Modifier::UNDERLINED),
        Tok::NameVariable => fg(theme::text_secondary()),
        Tok::Operator => plain().add_modifier(Modifier::BOLD),
        Tok::OperatorWord => fg(theme::text_error()).add_modifier(Modifier::BOLD),
    }
}

fn fg(color: Color) -> Style {
    Style::default().fg(color)
}
