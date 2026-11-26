//! Goal text: compact token counts, elapsed time, status labels and budgets.

use crate::server::{GoalStatus, PublicGoal};
use crate::utils::text::format_compact_count;

/// `950`, `12.5K`, `1.2M`.
pub fn tokens(value: u64) -> String {
    format_compact_count(value).replace('k', "K")
}

/// `45s`, `4m`, `1h05m`.
pub fn duration(seconds: u64) -> String {
    match seconds {
        0..=59 => format!("{seconds}s"),
        60..=3599 => format!("{}m", seconds / 60),
        _ => format!("{}h{:02}m", seconds / 3600, seconds % 3600 / 60),
    }
}

/// `12.5K / 50K` with a budget, else `12.5K`.
pub fn usage(goal: &PublicGoal) -> String {
    match goal.token_budget {
        Some(budget) => format!("{} / {}", tokens(goal.tokens_used), tokens(budget)),
        None => tokens(goal.tokens_used),
    }
}

/// The status word shown in the panel title.
pub fn status_label(status: GoalStatus) -> &'static str {
    match status {
        GoalStatus::Active => "active",
        GoalStatus::Paused => "paused",
        GoalStatus::Blocked => "blocked",
        GoalStatus::Complete => "complete",
        GoalStatus::BudgetLimited => "budget reached",
        GoalStatus::UsageLimited => "usage limit",
        GoalStatus::Unknown => "unknown",
    }
}

/// The goal row's right part: progress while it runs, else its status.
pub fn row_detail(goal: &PublicGoal) -> String {
    let time = duration(goal.time_used_seconds);
    match goal.status {
        GoalStatus::Active => format!("{} · {time}", usage(goal)),
        GoalStatus::Complete => format!("complete · {} · {time}", tokens(goal.tokens_used)),
        GoalStatus::Unknown => String::new(),
        status => status_label(status).to_owned(),
    }
}

/// Whether `/goal resume` (or `p` in the panel) applies: the goal stopped and can run again.
pub fn resumable(status: GoalStatus) -> bool {
    matches!(
        status,
        GoalStatus::Paused | GoalStatus::Blocked | GoalStatus::UsageLimited
    )
}

/// A budget as the edit field shows it: `200k`, `1.5m`, or plain digits.
pub fn budget_text(budget: Option<u64>) -> String {
    match budget {
        None => String::new(),
        Some(n) if n >= 1_000_000 && n % 100_000 == 0 => {
            let text = format!("{:.1}", n as f64 / 1_000_000.0);
            format!("{}m", text.trim_end_matches(".0"))
        }
        Some(n) if n >= 1_000 && n % 1_000 == 0 => format!("{}k", n / 1_000),
        Some(n) => n.to_string(),
    }
}

/// The edit field's budget: empty is no budget; digits with an optional `k`/`m` suffix.
pub fn parse_budget(text: &str) -> Result<Option<u64>, &'static str> {
    const INVALID: &str =
        "Enter a positive token budget like 200k, 1.5m or 50000, or leave it empty.";
    let text = text.trim().to_ascii_lowercase();
    if text.is_empty() {
        return Ok(None);
    }
    let (number, scale) = match text.strip_suffix('k') {
        Some(number) => (number, 1_000.0),
        None => match text.strip_suffix('m') {
            Some(number) => (number, 1_000_000.0),
            None => (text.as_str(), 1.0),
        },
    };
    let valid = !number.is_empty()
        && number.chars().all(|c| c.is_ascii_digit() || c == '.')
        && (scale > 1.0 || !number.contains('.'));
    let value = number
        .parse::<f64>()
        .ok()
        .filter(|_| valid)
        .ok_or(INVALID)?;
    let tokens = (value * scale).round();
    if tokens < 1.0 || tokens > u64::MAX as f64 {
        return Err(INVALID);
    }
    Ok(Some(tokens as u64))
}

/// `12.5K tokens` or `12.5K / 50K tokens`; nothing before the goal spent any.
fn spent(used: u64, budget: Option<u64>) -> Option<String> {
    if used == 0 {
        return None;
    }
    Some(match budget {
        Some(budget) => format!("{} / {} tokens", tokens(used), tokens(budget)),
        None => format!("{} tokens", tokens(used)),
    })
}

/// How a goal notice line is colored.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NoticeTone {
    Accent,
    Success,
    Warning,
}

/// A goal transcript notice: its glyph and tone, then its parts joined by ` · `.
pub fn notice_parts(
    detail: &crate::server::NoticeDetail,
    message: &str,
) -> (&'static str, NoticeTone, Vec<String>) {
    let event = detail.event.as_deref().unwrap_or_default();
    let tokens_used = detail.tokens_used.unwrap_or(0);
    let objective = detail.objective.clone().filter(|text| !text.is_empty());
    let head = |text: &str| vec![text.to_owned()];
    match event {
        "set" => {
            let mut parts = head("Goal set");
            parts.extend(objective);
            ("◆", NoticeTone::Accent, parts)
        }
        "edited" => {
            let mut parts = head("Goal edited");
            parts.extend(objective);
            ("◆", NoticeTone::Accent, parts)
        }
        "paused" => ("◆", NoticeTone::Accent, head("Goal paused")),
        "resumed" => ("◆", NoticeTone::Accent, head("Goal resumed")),
        "cleared" => ("◆", NoticeTone::Accent, head("Goal cleared")),
        "continued" => {
            let mut parts = head("Working on the goal");
            parts.extend(detail.continuation.map(|n| format!("continuation {n}")));
            parts.extend(spent(tokens_used, detail.token_budget));
            ("◆", NoticeTone::Accent, parts)
        }
        "completed" => {
            let mut parts = head("Goal complete");
            parts.extend(spent(tokens_used, None));
            parts.push(duration(detail.time_used_seconds.unwrap_or(0)));
            // Last, so a narrow terminal cuts the objective, not the counts.
            parts.extend(objective);
            ("✓", NoticeTone::Success, parts)
        }
        _ => {
            let text = if message.is_empty() {
                "Goal updated"
            } else {
                message
            };
            ("!", NoticeTone::Warning, head(text))
        }
    }
}
