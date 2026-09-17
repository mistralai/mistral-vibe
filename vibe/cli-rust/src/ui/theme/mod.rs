//! Central color palette, read live from the active Textual theme.

use ratatui::style::{Color, Modifier, Style};

mod registry;

pub use registry::{
    active, active_index, ansi_background, resolve, selection_fg, set_active_index,
    toast_background, ALL, AUTO_NAME,
};

/// Seed the detected `auto` palette, then activate the configured name.
pub fn prepare_active(name: &str, resolved_auto: &str) {
    registry::set_auto(resolved_auto);
    set_active(name);
}

/// Point the palette at the configured theme; unknown names resolve as `auto`.
pub fn set_active(name: &str) {
    let index = match resolve(name) {
        Some(index) => index,
        None => {
            tracing::warn!(theme = name, "unknown theme; falling back to auto");
            resolve(AUTO_NAME).unwrap_or_default()
        }
    };
    set_active_index(index);
}

/// Painted on every cell (Textual fills the whole screen with `$background`).
pub fn background() -> Color {
    active().background
}
/// The unstyled terminal surface, explicitly clearing prior frame modifiers.
pub fn screen_style() -> Style {
    Style::default()
        .bg(background())
        .remove_modifier(Modifier::all())
}
/// Text that must not inherit an ANSI dim modifier from a previous paint.
pub fn text(color: Color) -> Style {
    Style::default().fg(color).remove_modifier(Modifier::DIM)
}
/// Textual's reduced-opacity text uses SGR dim only on ANSI themes.
pub fn dim(color: Color) -> Style {
    let style = text(color);
    if is_ansi() {
        style.add_modifier(Modifier::DIM)
    } else {
        style
    }
}
/// Textual's muted labels use the muted color plus ANSI dim.
pub fn muted_style() -> Style {
    dim(muted())
}
/// Link cells under the mouse, matching Textual's link hover style.
pub fn link_hover_style() -> Style {
    let style = if is_ansi() {
        Style::default().fg(ansi_background()).bg(Color::LightBlue)
    } else {
        Style::default()
            .fg(foreground())
            .bg(active().primary)
            .add_modifier(Modifier::BOLD)
    };
    style.remove_modifier(Modifier::UNDERLINED)
}
/// Default text (banner meta, model, version, logo).
pub fn foreground() -> Color {
    active().foreground
}
/// The `/help` command hint (`$secondary`).
pub fn secondary() -> Color {
    active().secondary
}
/// $mistral_orange #FF8205 — the prompt marker and brand accent (theme-independent).
pub const ORANGE: Color = Color::Rgb(0xFF, 0x82, 0x05);
/// $text-muted — the muted blend Textual computes for dim/secondary labels.
pub fn muted() -> Color {
    blend(background(), auto_contrast(), 0.6)
}
/// $foreground-muted — the completion popup's border (60% foreground over bg).
pub fn popup_border() -> Color {
    active().popup_border
}
/// Text-selection highlight background (`$screen-selection-background`).
pub fn selection_bg() -> Color {
    active().selection_bg
}

/// $warning — the interrupt marker text and other warning-level content.
pub fn warning() -> Color {
    active().warning
}
/// $success — a safe agent's input border, completed effects, added diff lines.
pub fn success() -> Color {
    active().success
}
pub fn status_ready() -> Color {
    success()
}
pub fn error() -> Color {
    active().error
}

// Completed-effect row: dim $primary verb and muted grey message.
pub fn effect_verb() -> Color {
    if is_ansi() {
        return primary();
    }
    blend(background(), primary(), 0.55)
}
pub fn effect_message() -> Color {
    blend(background(), foreground(), 0.55)
}

/// $primary — used bold for the keys in shortcut hints and the block cursor.
pub fn primary() -> Color {
    active().primary
}
/// $surface — the `/config` modal background (Textual `background: $surface`).
pub fn surface() -> Color {
    active().surface
}

/// $border-blurred — an unfocused border, ANSI black on the terminal-color themes.
pub fn border_blurred() -> Color {
    match is_ansi() {
        true => Color::Black,
        false => popup_border(),
    }
}

/// OptionList cursor background ($block-cursor-background) and its text color.
pub fn block_cursor_bg() -> Color {
    active().block_cursor_bg
}
pub fn block_cursor_fg() -> Color {
    active().block_cursor_fg
}

/// Input caret block, including Textual's light-theme `$foreground 70%` rule.
pub fn input_cursor_bg() -> Color {
    if !is_ansi() && !is_dark() {
        blend(background(), foreground(), 0.7)
    } else {
        active().input_cursor_bg
    }
}
/// TextArea's current-line `$boost`; only Textual's default dark theme sets it.
pub fn input_cursor_line_bg() -> Color {
    if active().name == "textual-dark" {
        blend(background(), Color::Rgb(0xFF, 0xFF, 0xFF), 10.0 / 255.0)
    } else {
        background()
    }
}
/// ANSI themes render the caret as reverse-video (Textual's `:ansi` TextArea rule),
/// identified by their terminal-default (Reset) background.
pub fn input_cursor_reverse() -> bool {
    is_ansi()
}

/// True for the `ansi-dark`/`ansi-light` themes, which Textual renders with raw
/// terminal colors (their background is the terminal default, `Reset`). Several
/// `:ansi` TCSS rules differ from truecolor themes (fence margin, code color).
pub fn is_ansi() -> bool {
    active().background == Color::Reset
}

/// Scrollbar thumb ($scrollbar) and track ($scrollbar-background).
pub fn scrollbar() -> Color {
    active().scrollbar
}
pub fn scrollbar_bg() -> Color {
    active().scrollbar_bg
}

// Markdown constructs in assistant messages, matching Textual's `Markdown` widget.

/// Heading text. Truecolor themes use $primary for every level; ANSI themes map
/// each level to a raw terminal color (Textual's `design.py` ANSI palette).
pub fn md_heading(level: u8) -> Color {
    if !is_ansi() {
        return active().primary;
    }
    match level {
        1 => Color::Magenta,
        2 => Color::LightBlue,
        3 => Color::Blue,
        _ => Color::Cyan,
    }
}
/// Inline code (`x`) — vibe overrides the widget default to $success.
pub fn md_code_inline() -> Color {
    active().success
}
/// Unstyled highlighted text inherits `$text`: terminal default or theme contrast.
pub fn code_plain() -> Color {
    if is_ansi() {
        foreground()
    } else {
        auto_contrast()
    }
}
/// Blockquote bar `▌` — $foreground-muted (60% foreground over background).
pub fn md_quote_bar() -> Color {
    active().popup_border
}
/// Link label — the dimmed foreground Textual paints link text with.
pub fn md_link() -> Color {
    if is_ansi() {
        return primary();
    }
    blend(background(), auto_contrast(), 0.87)
}
/// Table keyline border — $foreground 20% over background.
pub fn md_table_border() -> Color {
    blend(background(), foreground(), 0.2)
}

// Blends Textual computes at render time rather than storing in the theme table.

/// Textual's `DimFilter` factor: a `dim` style blends its color toward the cell background.
pub const DIM_FACTOR: f32 = 0.66;
/// Textual's `Color.blend`: `base` moved `alpha` of the way toward `other`.
pub fn blend(base: Color, other: Color, alpha: f32) -> Color {
    let (Color::Rgb(r1, g1, b1), Color::Rgb(r2, g2, b2)) = (base, other) else {
        return base;
    };
    let mix = |a: u8, b: u8| (a as f32 + (b as f32 - a as f32) * alpha) as u8;
    Color::Rgb(mix(r1, r2), mix(g1, g2), mix(b1, b2))
}

/// Whether the active theme is a dark one (drives Textual's contrast and band alphas).
pub fn is_dark() -> bool {
    active().dark
}

/// Textual's `auto` color: the contrast text of the theme background.
pub fn auto_contrast() -> Color {
    if is_dark() {
        Color::Rgb(0xFF, 0xFF, 0xFF)
    } else {
        Color::Rgb(0x00, 0x00, 0x00)
    }
}

/// `$text-muted` as Textual's markup resolves it: the bare contrast text (terminal default on ANSI).
pub fn text_muted() -> Color {
    if is_ansi() {
        muted()
    } else {
        auto_contrast()
    }
}

/// `$text-success` / `$text-error`, stored because Textual's own compositing
/// lands a channel off the `tint` below on some themes.
pub fn text_success() -> Color {
    active().text_success
}
pub fn text_error() -> Color {
    active().text_error
}

/// The remaining `$text-*` tints Textual computes as `contrast_text.tint(color 66%)`;
/// ANSI themes use the raw color instead.
pub fn text_primary() -> Color {
    tint(primary())
}
pub fn text_secondary() -> Color {
    tint(secondary())
}
pub fn text_warning() -> Color {
    tint(warning())
}
pub fn text_accent() -> Color {
    tint(active().accent)
}
fn tint(color: Color) -> Color {
    if is_ansi() {
        color
    } else {
        blend(auto_contrast(), color, 0.66)
    }
}

/// Warm gradient the loading label cycles through (yellow → orange → red).
pub const LOADING_GRADIENT: [Color; 5] = [
    Color::Rgb(0xFF, 0xD8, 0x00), // YELLOW
    Color::Rgb(0xFF, 0xAF, 0x00), // ORANGE_LIGHT
    Color::Rgb(0xFF, 0x82, 0x05), // ORANGE
    Color::Rgb(0xFA, 0x50, 0x0F), // ORANGE_DARK
    Color::Rgb(0xE1, 0x05, 0x00), // RED
];
