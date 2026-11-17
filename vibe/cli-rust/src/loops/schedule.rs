//! How a loop's schedule and next run read: `every 20m`, `weekdays 09:00`, `in 5m`.

use crate::server::ScheduledLoop;

const MINUTE: f64 = 60.0;
const HOUR: f64 = 60.0 * MINUTE;
const DAY: f64 = 24.0 * HOUR;

/// The loop's recurrence: `every 20m`, or its cron in words when common, else the cron itself.
pub fn recurrence(scheduled: &ScheduledLoop) -> String {
    if let Some(cron) = &scheduled.cron {
        return cron_words(cron).unwrap_or_else(|| format!("cron {cron}"));
    }
    match scheduled.interval_seconds {
        Some(seconds) => format!("every {}", interval(seconds)),
        None => String::new(),
    }
}

/// `30s`, `20m`, `2h`, `1d`, or the largest exact unit: `90s`, `1h30m` reads `90m`.
pub fn interval(seconds: u64) -> String {
    for (unit, size) in [("d", 86_400), ("h", 3_600), ("m", 60)] {
        if seconds >= size && seconds.is_multiple_of(size) {
            return format!("{}{unit}", seconds / size);
        }
    }
    format!("{seconds}s")
}

/// `daily 09:00`, `weekdays 09:00`, `hourly at :15` for a fixed minute and hour; `None` otherwise.
fn cron_words(cron: &str) -> Option<String> {
    let fields: Vec<&str> = cron.split_whitespace().collect();
    let [minute, hour, day, month, weekday] = fields.as_slice() else {
        return None;
    };
    let minute: u8 = minute.parse().ok().filter(|m| *m < 60)?;
    if (*day, *month) != ("*", "*") {
        return None;
    }
    if *hour == "*" {
        return (*weekday == "*").then(|| format!("hourly at :{minute:02}"));
    }
    let hour: u8 = hour.parse().ok().filter(|h| *h < 24)?;
    let days = match *weekday {
        "*" => "daily",
        "1-5" => "weekdays",
        "0,6" | "6,0" => "weekends",
        _ => return None,
    };
    Some(format!("{days} {hour:02}:{minute:02}"))
}

/// The next run, `seconds_left` away, coarse so the text rarely changes.
pub fn next_run(seconds_left: f64) -> String {
    match seconds_left {
        s if s <= 0.0 => "due".into(),
        s if s < MINUTE => "in <1m".into(),
        s if s <= HOUR - MINUTE => format!("in {}m", (s / MINUTE).ceil()),
        s if s < DAY => format!("in ~{}h", (s / HOUR).round()),
        s => format!("in ~{}d", (s / DAY).round()),
    }
}

/// How long until `next_run`'s text changes, so an idle screen redraws then.
pub fn next_change(seconds_left: f64) -> f64 {
    if seconds_left <= 0.0 {
        return f64::INFINITY;
    }
    let boundary = boundary_below(seconds_left);
    let wait = seconds_left - boundary;
    // A boundary that still shows the same text is inclusive: wake just past it.
    match next_run(boundary) == next_run(seconds_left) {
        true => wait + PAST_BOUNDARY,
        false => wait,
    }
}

/// How far past an inclusive boundary to wake, so the new text shows.
const PAST_BOUNDARY: f64 = 0.01;

/// The nearest `next_run` threshold at or below `seconds_left`: where its text changes next.
fn boundary_below(seconds_left: f64) -> f64 {
    match seconds_left {
        s if s < MINUTE => 0.0,
        s if s <= HOUR - MINUTE => (MINUTE * ((s / MINUTE).ceil() - 1.0)).max(MINUTE),
        s if s < DAY => match (s / HOUR).round() {
            hours if hours <= 1.0 => HOUR - MINUTE,
            hours => HOUR * (hours - 0.5),
        },
        s => match (s / DAY).round() {
            days if days <= 1.0 => DAY,
            days => DAY * (days - 0.5),
        },
    }
}

/// How long until any loop's `next_run` text can change; a due loop never changes, but later rows still do.
pub fn next_redraw(loops: &[ScheduledLoop], now: f64) -> f64 {
    loops
        .iter()
        .map(|scheduled| next_change(scheduled.next_fire_at - now))
        .fold(f64::INFINITY, f64::min)
}

/// The loop's title: the first line of its prompt.
pub fn title(scheduled: &ScheduledLoop) -> &str {
    crate::transcript::split_loop_prompt(&scheduled.prompt).0
}

/// The loop's instructions: its prompt after the title line.
pub fn instructions(scheduled: &ScheduledLoop) -> &str {
    crate::transcript::split_loop_prompt(&scheduled.prompt).1
}
