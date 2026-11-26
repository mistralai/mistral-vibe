//! Markdown color roles stay readable and distinct on every built-in theme.

use ratatui::style::{Color, Modifier};
use vibe_rs::ui::theme;

fn luminance(color: Color) -> f64 {
    let Color::Rgb(r, g, b) = color else {
        panic!("expected an RGB color, got {color:?}");
    };
    let channel = |c: u8| {
        let c = c as f64 / 255.0;
        if c <= 0.03928 {
            c / 12.92
        } else {
            ((c + 0.055) / 1.055).powf(2.4)
        }
    };
    0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

fn contrast(a: Color, b: Color) -> f64 {
    let (a, b) = (luminance(a), luminance(b));
    (a.max(b) + 0.05) / (a.min(b) + 0.05)
}

#[test]
fn truecolor_text_roles_meet_wcag_aa_contrast() {
    for name in theme::ALL
        .iter()
        .map(|t| t.name)
        .filter(|n| !n.starts_with("ansi"))
    {
        theme::set_active(name);
        let bg = theme::background();
        for (role, color) in [
            ("heading", theme::md_accent()),
            ("code", theme::md_code_inline()),
            ("link", theme::md_link()),
        ] {
            assert!(
                contrast(color, bg) >= 4.5,
                "{name} {role}: {:.2}",
                contrast(color, bg)
            );
        }
        assert!(
            contrast(theme::md_keyline().fg.unwrap(), bg) >= 1.5,
            "{name} keyline"
        );
        assert_ne!(
            theme::md_keyline().fg,
            Some(theme::md_fence_border()),
            "{name}"
        );
    }
}

#[test]
fn ansi_roles_follow_the_terminal_brightness() {
    theme::set_active("ansi-dark");
    assert_eq!(theme::md_accent(), Color::LightMagenta);
    assert_eq!(theme::md_link(), Color::LightBlue);
    assert_eq!(theme::md_code_inline(), Color::Yellow);
    assert!(theme::md_keyline().add_modifier.contains(Modifier::DIM));

    theme::set_active("ansi-light");
    assert_eq!(theme::md_accent(), Color::Magenta);
    assert_eq!(theme::md_link(), Color::Blue);
    assert_eq!(theme::md_code_inline(), Color::Red);
    assert!(theme::md_keyline().add_modifier.contains(Modifier::DIM));
}

#[test]
fn headings_rank_by_style() {
    theme::set_active("textual-dark");
    let h1 = theme::md_heading(1);
    let h2 = theme::md_heading(2);
    assert!(h1
        .add_modifier
        .contains(Modifier::BOLD | Modifier::UNDERLINED));
    assert!(!h2.add_modifier.contains(Modifier::UNDERLINED));
    assert_eq!(h1.fg, h2.fg);
    assert_eq!(theme::md_heading(3).fg, Some(theme::foreground()));
    assert_eq!(theme::md_heading(4).fg, Some(theme::muted()));
    assert_eq!(theme::md_heading(6), theme::md_heading(4));
}

#[test]
fn highlighted_code_tokens_meet_wcag_aa_contrast() {
    let code = "import os\n@cache\nclass G:\n    def f(self, n: int = 2) -> str:\n        return f\"hi {n}\" if n else None\n";
    for name in theme::ALL
        .iter()
        .map(|t| t.name)
        .filter(|n| !n.starts_with("ansi"))
    {
        theme::set_active(name);
        let bg = theme::background();
        let lines = vibe_rs::ui::highlight::code(code, "python").unwrap();
        for span in lines
            .iter()
            .flatten()
            .filter(|s| !s.content.trim().is_empty())
        {
            let fg = span.style.fg.unwrap();
            assert!(
                contrast(fg, bg) >= 4.5,
                "{name} {:?}: {:.2}",
                span.content,
                contrast(fg, bg)
            );
        }
    }
}

#[test]
fn rust_strings_take_the_string_tint_on_truecolor_and_stay_plain_on_ansi() {
    let code = "fn f() { let s = \"a & u8\"; }";
    let string_fg = || {
        let lines = vibe_rs::ui::highlight::code(code, "rust").unwrap();
        let spans = &lines[0];
        let open = spans
            .iter()
            .position(|s| s.content.starts_with('"'))
            .unwrap();
        let close = open
            + spans[open..]
                .iter()
                .position(|s| s.content.ends_with('"'))
                .unwrap();
        let fgs: Vec<_> = spans[open..=close].iter().map(|s| s.style.fg).collect();
        assert!(fgs.windows(2).all(|w| w[0] == w[1]), "{fgs:?}");
        fgs[0]
    };
    theme::set_active("textual-dark");
    assert_ne!(string_fg(), Some(theme::code_plain()));
    theme::set_active("ansi-dark");
    assert_eq!(string_fg(), Some(theme::code_plain()));
}
