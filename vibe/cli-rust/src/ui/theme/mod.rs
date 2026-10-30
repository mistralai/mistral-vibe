//! Central color palette, read live from the active theme.

use ratatui::style::{Color, Modifier, Style};

pub mod color_system;
pub mod fixed;
mod markdown;
mod registry;
pub(crate) mod syntax;

pub use fixed::{LOADING_GRADIENT, ORANGE};
pub use markdown::{
    code_plain, md_accent, md_code_inline, md_fence_border, md_heading, md_keyline, md_link,
    md_table_header, md_widget_code_inline, md_widget_fence_bg, md_widget_marker,
    md_widget_quote_bar,
};

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

/// Painted on every cell: the whole screen fills with `$background`.
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
/// Reduced-opacity text uses SGR dim only on ANSI themes.
pub fn dim(color: Color) -> Style {
    let style = text(color);
    if is_ansi() {
        style.add_modifier(Modifier::DIM)
    } else {
        style
    }
}
/// Muted labels use the muted color plus ANSI dim.
pub fn muted_style() -> Style {
    dim(muted())
}
/// Link cells under the mouse.
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
/// $text-muted — the muted blend for dim/secondary labels.
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
/// $surface — the `/config` modal background.
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
/// OptionList hover over `base`: contrast text at 10%, `$ansi-background` on ANSI themes (Textual shows none on ansi-dark).
pub fn block_hover_bg(base: Color) -> Color {
    match is_ansi() {
        true => ansi_background(),
        false => blend(base, auto_contrast(), 0.1),
    }
}
/// A text field's block caret (and its selection) over `base`, in block-cursor colors.
pub fn block_caret(base: Style) -> Style {
    base.fg(block_cursor_fg()).bg(block_cursor_bg())
}

/// Input caret block, including the light-theme `$foreground 70%` rule.
pub fn input_cursor_bg() -> Color {
    if !is_ansi() && !is_dark() {
        blend(background(), foreground(), 0.7)
    } else {
        active().input_cursor_bg
    }
}
/// The composer's current-line `$boost`; only the default dark theme sets it.
pub fn input_cursor_line_bg() -> Color {
    if active().name == "textual-dark" {
        blend(background(), Color::Rgb(0xFF, 0xFF, 0xFF), 10.0 / 255.0)
    } else {
        background()
    }
}
/// ANSI themes render the caret as reverse-video,
/// identified by their terminal-default (Reset) background.
pub fn input_cursor_reverse() -> bool {
    is_ansi()
}

/// True for the `ansi-dark`/`ansi-light` themes, which render with raw
/// terminal colors (their background is the terminal default, `Reset`). Some
/// styles differ from truecolor themes (fence margin, code color).
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

// Blends computed at render time rather than stored in the theme table.

/// Dim factor: a `dim` style blends its color toward the cell background.
pub const DIM_FACTOR: f32 = 0.66;
/// `base` moved `alpha` of the way toward `other`.
pub fn blend(base: Color, other: Color, alpha: f32) -> Color {
    let (Color::Rgb(r1, g1, b1), Color::Rgb(r2, g2, b2)) = (base, other) else {
        return base;
    };
    let mix = |a: u8, b: u8| (a as f32 + (b as f32 - a as f32) * alpha) as u8;
    Color::Rgb(mix(r1, r2), mix(g1, g2), mix(b1, b2))
}

/// Whether the active theme is a dark one (drives contrast and band alphas).
pub fn is_dark() -> bool {
    active().dark
}

/// The `auto` color: the contrast text of the theme background.
pub fn auto_contrast() -> Color {
    if is_dark() {
        Color::Rgb(0xFF, 0xFF, 0xFF)
    } else {
        Color::Rgb(0x00, 0x00, 0x00)
    }
}

/// `$text-muted` as markup resolves it: the bare contrast text (terminal default on ANSI).
pub fn text_muted() -> Color {
    if is_ansi() {
        muted()
    } else {
        auto_contrast()
    }
}

/// `$text-success` / `$text-error`, stored because the reference values sit a
/// channel off the `tint` below on some themes.
pub fn text_success() -> Color {
    active().text_success
}
pub fn text_error() -> Color {
    active().text_error
}

/// The remaining `$text-*` tints, computed as `contrast_text.tint(color 66%)`;
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
