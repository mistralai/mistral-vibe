//! Decimal byte sizes, like the Finder: `17.6 GB`, `889 MB`.

const UNITS: [&str; 5] = ["B", "KB", "MB", "GB", "TB"];

/// `bytes` in the largest decimal unit under 1000; one decimal under 100 GB, none at or above, or below GB.
pub fn decimal(bytes: u64) -> String {
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 999.5 && unit < UNITS.len() - 1 {
        value /= 1000.0;
        unit += 1;
    }
    if unit < 3 || value >= 99.95 {
        return format!("{value:.0} {}", UNITS[unit]);
    }
    let text = format!("{value:.1}");
    format!(
        "{} {}",
        text.strip_suffix(".0").unwrap_or(&text),
        UNITS[unit]
    )
}

/// The telemetry count bucket, the same bounds as the app server's scan event.
pub fn count_bucket(count: usize) -> &'static str {
    match count {
        0 => "0",
        1..=10 => "1–10",
        11..=50 => "11–50",
        51..=100 => "51–100",
        _ => "100+",
    }
}

/// The telemetry size bucket, the same bounds as the app server's scan event.
pub fn bucket(bytes: u64) -> &'static str {
    const GB: u64 = 1_000_000_000;
    match bytes {
        b if b < GB => "<1 GB",
        b if b < 5 * GB => "1–5 GB",
        b if b < 20 * GB => "5–20 GB",
        b if b < 50 * GB => "20–50 GB",
        _ => "50+ GB",
    }
}
