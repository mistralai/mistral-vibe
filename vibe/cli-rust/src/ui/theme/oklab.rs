//! sRGB and OKLab conversions, for color steps that look even to the eye.

/// WCAG relative luminance.
pub fn luminance(rgb: [u8; 3]) -> f32 {
    let [r, g, b] = rgb.map(|c| linear(f32::from(c)));
    0.2126 * r + 0.7152 * g + 0.0722 * b
}

/// The sRGB color of an OKLab one, losing colorfulness until it fits.
pub fn in_gamut([l, a, b]: [f32; 3]) -> [u8; 3] {
    let mut chroma = 1.0;
    loop {
        let rgb = from_oklab([l, a * chroma, b * chroma]);
        if rgb.iter().all(|c| (0.0..=1.0).contains(c)) || chroma < 0.01 {
            return rgb.map(|c| (encode(c.clamp(0.0, 1.0)) * 255.0).round() as u8);
        }
        chroma *= 0.9;
    }
}

/// The OKLab color of an sRGB one.
pub fn oklab_of(rgb: [u8; 3]) -> [f32; 3] {
    oklab(rgb.map(|c| linear(f32::from(c))))
}

/// An sRGB channel from 0 to 255, as linear light.
fn linear(c: f32) -> f32 {
    let c = c / 255.0;
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

/// Linear light as an sRGB channel from 0 to 1.
fn encode(c: f32) -> f32 {
    if c <= 0.003_130_8 {
        12.92 * c
    } else {
        1.055 * c.powf(1.0 / 2.4) - 0.055
    }
}

fn oklab([r, g, b]: [f32; 3]) -> [f32; 3] {
    let l = (0.412_221_46 * r + 0.536_332_55 * g + 0.051_445_995 * b).cbrt();
    let m = (0.211_903_5 * r + 0.680_699_5 * g + 0.107_396_96 * b).cbrt();
    let s = (0.088_302_46 * r + 0.281_718_85 * g + 0.629_978_7 * b).cbrt();
    [
        0.210_454_26 * l + 0.793_617_8 * m - 0.004_072_047 * s,
        1.977_998_5 * l - 2.428_592_2 * m + 0.450_593_7 * s,
        0.025_904_037 * l + 0.782_771_77 * m - 0.808_675_77 * s,
    ]
}

/// The linear sRGB color of an OKLab one, possibly out of gamut.
fn from_oklab([l, a, b]: [f32; 3]) -> [f32; 3] {
    let l_ = (l + 0.396_337_78 * a + 0.215_803_76 * b).powi(3);
    let m_ = (l - 0.105_561_346 * a - 0.063_854_17 * b).powi(3);
    let s_ = (l - 0.089_484_18 * a - 1.291_485_5 * b).powi(3);
    [
        4.076_741_7 * l_ - 3.307_711_6 * m_ + 0.230_969_94 * s_,
        -1.268_438 * l_ + 2.609_757_4 * m_ - 0.341_319_38 * s_,
        -0.004_196_086_3 * l_ - 0.703_418_6 * m_ + 1.707_614_7 * s_,
    ]
}
