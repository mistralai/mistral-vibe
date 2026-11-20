//! The copy animation: the light's path, softness and gradient, its readability, and the animation's lifetime.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Style};

use vibe_rs::ui::copy_light::gradient::Gradient;
use vibe_rs::ui::copy_light::wave::{self, Light};
use vibe_rs::ui::copy_light::CopyLight;

const SELECTION: [u8; 3] = [0x09, 0x44, 0x72];
const ACCENT: [u8; 3] = [0xFA, 0x50, 0x0F];
const ROW: [(u16, u16, u16); 1] = [(0, 0, 59)];
const FRAME_MS: f32 = 1000.0 / 60.0;
const MID_MS: f32 = 200.0;

fn rgb([r, g, b]: [u8; 3]) -> Color {
    Color::Rgb(r, g, b)
}

fn selected_row() -> Buffer {
    let mut buf = Buffer::empty(Rect::new(0, 0, 60, 1));
    buf.set_style(
        buf.area,
        Style::default()
            .fg(Color::Rgb(0x80, 0x80, 0x80))
            .bg(Color::Rgb(0x09, 0x44, 0x72)),
    );
    buf
}

fn brightest(light: Light, y: u16, ms: f32) -> u16 {
    (0..60)
        .max_by(|&a, &b| {
            light
                .strength(a, y, ms, FRAME_MS)
                .total_cmp(&light.strength(b, y, ms, FRAME_MS))
        })
        .unwrap()
}

#[test]
fn the_light_crosses_left_to_right_from_the_copy() {
    let light = Light::new(&ROW);
    assert!((0..60).all(|x| light.strength(x, 0, 0.0, FRAME_MS) == 0.0));
    assert!(brightest(light, 0, 150.0) < brightest(light, 0, 200.0));
    assert!(brightest(light, 0, 200.0) < brightest(light, 0, 250.0));
    let after = wave::DURATION_MS + FRAME_MS;
    assert!((0..60).all(|x| light.strength(x, 0, after, FRAME_MS) == 0.0));
}

#[test]
fn the_light_leans_so_lower_rows_meet_it_later() {
    let light = Light::new(&[(0, 0, 59), (4, 0, 59)]);
    assert!(brightest(light, 4, MID_MS) < brightest(light, 0, MID_MS));
}

#[test]
fn the_light_glides_without_steps_between_cells_or_frames() {
    for spans in [
        vec![(0, 0, 5)],
        ROW.to_vec(),
        (0..20).map(|y| (y, 0, 159)).collect(),
    ] {
        let light = Light::new(&spans);
        let (mut cell_step, mut frame_step) = (0.0f32, 0.0f32);
        for frame in 0..24 {
            let ms = frame as f32 * FRAME_MS;
            for x in 0..spans[0].2 {
                let here = light.strength(x, 0, ms, FRAME_MS);
                cell_step = cell_step.max((light.strength(x + 1, 0, ms, FRAME_MS) - here).abs());
                let next = light.strength(x, 0, ms + FRAME_MS, FRAME_MS);
                frame_step = frame_step.max((next - here).abs());
            }
        }
        assert!(
            cell_step < 0.25,
            "{} cells: cell step {cell_step}",
            spans[0].2 + 1
        );
        assert!(
            frame_step < 0.4,
            "{} cells: frame step {frame_step}",
            spans[0].2 + 1
        );
    }
}

#[test]
fn the_gradient_runs_from_the_selection_to_a_lighter_shade_tinted_toward_the_accent() {
    let plain = Gradient::new(SELECTION, None, None);
    assert_eq!(plain.at(0.0), rgb(SELECTION));
    assert_eq!(plain.at(1.0), plain.light);
    let Color::Rgb(r, g, b) = plain.light else {
        panic!("the light shade is RGB");
    };
    assert!(b > g && g > r, "same blue hue");
    assert!(luminance(plain.light) > luminance(rgb(SELECTION)));
    let (Color::Rgb(tinted_r, ..), Color::Rgb(plain_r, ..)) = (
        Gradient::new(SELECTION, Some(ACCENT), None).light,
        plain.light,
    ) else {
        panic!("the light shade is RGB");
    };
    assert!(tinted_r > plain_r, "pulled toward the orange accent");
}

#[test]
fn mid_animation_only_the_background_changes_brightest_where_the_light_is() {
    let gradient = Gradient::new(SELECTION, Some(ACCENT), None);
    let mut buf = selected_row();
    wave::paint(&mut buf, &ROW, MID_MS, FRAME_MS, gradient);
    assert!((0..60).all(|x| buf[(x, 0)].modifier == selected_row()[(x, 0)].modifier));
    let peak = &buf[(brightest(Light::new(&ROW), 0, MID_MS), 0)];
    assert!((0..60).all(|x| luminance(buf[(x, 0)].bg) <= luminance(peak.bg)));
    assert!(luminance(peak.bg) > luminance(rgb(SELECTION)));
    assert_eq!(peak.fg, Color::Rgb(0x80, 0x80, 0x80));
}

#[test]
fn the_selection_is_untouched_before_and_after_the_animation() {
    let gradient = Gradient::new(SELECTION, Some(ACCENT), None);
    for ms in [0.0, wave::DURATION_MS + FRAME_MS] {
        let mut buf = selected_row();
        wave::paint(&mut buf, &ROW, ms, FRAME_MS, gradient);
        assert_eq!(buf, selected_row());
    }
}

#[test]
fn a_copy_starts_the_animation_and_nothing_paints_without_one() {
    let mut light = CopyLight::default();
    let mut buf = selected_row();
    light.paint(&mut buf, &ROW);
    assert_eq!(buf, selected_row());
    assert!(!light.active());
    light.start();
    assert!(light.active());
    light.step();
    assert!(light.active(), "still running right after the copy");
}

#[test]
fn a_new_press_stops_the_running_animation() {
    let mut light = CopyLight::default();
    light.start();
    light.stop();
    assert!(!light.active());
}

#[test]
fn a_copy_flashes_only_while_the_selection_animation_is_on() {
    let mut app = vibe_rs::app::App::default();
    app.session.startup_config.disable_selection_animation = true;
    vibe_rs::ui::selection::flash(&mut app);
    assert!(!app.view.copy_light.active());
    app.session.startup_config.disable_selection_animation = false;
    vibe_rs::ui::selection::flash(&mut app);
    assert!(app.view.copy_light.active());
}

#[test]
fn a_frame_dropped_under_load_streaks_instead_of_jumping() {
    let light = Light::new(&ROW);
    let jump = |blur_ms: f32| {
        (0..60)
            .map(|x| {
                let before = light.strength(x, 0, MID_MS, FRAME_MS);
                (light.strength(x, 0, MID_MS + 2.0 * FRAME_MS, blur_ms) - before).abs()
            })
            .fold(0.0f32, f32::max)
    };
    assert!(jump(2.0 * FRAME_MS) < jump(FRAME_MS));
}

fn linear(c: u8) -> f32 {
    let c = f32::from(c) / 255.0;
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

fn channels(color: Color) -> [f32; 3] {
    let Color::Rgb(r, g, b) = color else {
        panic!("the gradient is RGB");
    };
    [linear(r), linear(g), linear(b)]
}

fn luminance(color: Color) -> f32 {
    let [r, g, b] = channels(color);
    0.2126 * r + 0.7152 * g + 0.0722 * b
}

fn contrast(a: Color, b: Color) -> f32 {
    let (a, b) = (luminance(a), luminance(b));
    (a.max(b) + 0.05) / (a.min(b) + 0.05)
}

/// Whether black text reads better than white on `color`, the choice terminals make when they recolor text.
fn prefers_black(color: Color) -> bool {
    contrast(color, Color::Rgb(0, 0, 0)) > contrast(color, Color::Rgb(255, 255, 255))
}

fn oklab_distance(a: Color, b: Color) -> f32 {
    let lab = |color: Color| {
        let [r, g, b] = channels(color);
        let l = (0.412_221_46 * r + 0.536_332_55 * g + 0.051_445_995 * b).cbrt();
        let m = (0.211_903_5 * r + 0.680_699_5 * g + 0.107_396_96 * b).cbrt();
        let s = (0.088_302_46 * r + 0.281_718_85 * g + 0.629_978_7 * b).cbrt();
        [
            0.210_454_26 * l + 0.793_617_8 * m - 0.004_072_047 * s,
            1.977_998_5 * l - 2.428_592_2 * m + 0.450_593_7 * s,
            0.025_904_037 * l + 0.782_771_77 * m - 0.808_675_77 * s,
        ]
    };
    let (a, b) = (lab(a), lab(b));
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()
}

/// Every truecolor theme's gradient with its selection and text colors.
fn theme_gradients() -> Vec<(&'static str, Color, Color, Gradient)> {
    use vibe_rs::ui::theme::{self, ALL};
    ALL.iter()
        .filter_map(|t| {
            let selection = theme::channels(t.selection_bg)?;
            let text = theme::channels(theme::selection_fg_of(t))?;
            let gradient = Gradient::new(selection, theme::channels(t.primary), Some(text));
            Some((t.name, rgb(selection), rgb(text), gradient))
        })
        .collect()
}

fn steps(gradient: Gradient) -> impl Iterator<Item = Color> {
    (0..=20).map(move |step| gradient.at(step as f32 / 20.0))
}

#[test]
fn on_every_theme_the_band_never_lowers_the_text_contrast() {
    for (name, selection, text, gradient) in theme_gradients() {
        let own = contrast(selection, text);
        for color in steps(gradient) {
            assert!(
                contrast(color, text) >= own - 0.02,
                "{name}: {color:?} lowers the contrast"
            );
        }
    }
}

#[test]
fn on_every_theme_a_terminal_recoloring_text_keeps_the_same_text_color() {
    for (name, selection, _, gradient) in theme_gradients() {
        for color in steps(gradient) {
            assert_eq!(
                prefers_black(color),
                prefers_black(selection),
                "{name}: {color:?} flips black or white text"
            );
        }
    }
}

#[test]
fn on_every_theme_the_band_is_visible_but_sober_and_reads_as_one_strip() {
    for (name, selection, _, gradient) in theme_gradients() {
        let peak = oklab_distance(selection, gradient.light);
        assert!(
            (0.045..=0.085).contains(&peak),
            "{name}: peak difference {peak}"
        );
        let distances: Vec<f32> = steps(gradient)
            .map(|c| oklab_distance(selection, c))
            .collect();
        assert!(
            distances.windows(2).all(|w| w[1] >= w[0] - 0.002),
            "{name}: the band is not one strip"
        );
    }
}
