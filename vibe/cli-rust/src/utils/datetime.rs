//! Wall-clock formatting for epoch-millisecond timestamps.

/// Local `%Y-%m-%d %H:%M:%S %Z`, as the app-server formats scheduled runs.
#[cfg(unix)]
pub fn format_local(ms: u64) -> String {
    extern "C" {
        fn tzset();
    }
    let seconds = (ms / 1_000) as libc::time_t;
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    unsafe { tzset() };
    if unsafe { libc::localtime_r(&seconds, &mut tm) }.is_null() {
        return format_utc(ms);
    }
    let mut buffer = [0u8; 64];
    let written = unsafe {
        libc::strftime(
            buffer.as_mut_ptr().cast(),
            buffer.len(),
            c"%Y-%m-%d %H:%M:%S %Z".as_ptr(),
            &tm,
        )
    };
    if written == 0 {
        return format_utc(ms);
    }
    String::from_utf8_lossy(&buffer[..written]).into_owned()
}

#[cfg(not(unix))]
pub fn format_local(ms: u64) -> String {
    format_utc(ms)
}

/// `%Y-%m-%d %H:%M:%S UTC`.
pub fn format_utc(ms: u64) -> String {
    let seconds = ms / 1_000;
    let (year, month, day) = civil_from_days((seconds / 86_400) as i64);
    let time = seconds % 86_400;
    format!(
        "{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02} UTC",
        time / 3_600,
        time % 3_600 / 60,
        time % 60
    )
}

/// Howard Hinnant's `civil_from_days`: days since 1970-01-01 to a proleptic Gregorian date.
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_index = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_index + 2) / 5 + 1;
    let month = if month_index < 10 {
        month_index + 3
    } else {
        month_index - 9
    };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (year, month, day)
}
