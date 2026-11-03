//! UI time: under replay it stands still until the harness advances it, so a timed behavior is a scripted step.

use std::ops::Add;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::LazyLock;
use std::time::{Duration, Instant};

use tokio::sync::Notify;

static FROZEN: LazyLock<bool> = LazyLock::new(crate::utils::is_replaying);
static START: LazyLock<Instant> = LazyLock::new(real_now);
static ADVANCED_MS: AtomicU64 = AtomicU64::new(0);
static ADVANCED: Notify = Notify::const_new();

/// An instant on the UI clock, its own type so it never mixes with a machine `Instant`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct UiInstant(Instant);

impl UiInstant {
    /// UI time from `earlier` to `self`, zero when `earlier` is later.
    pub fn duration_since(self, earlier: Self) -> Duration {
        self.0.saturating_duration_since(earlier.0)
    }

    /// `by` before `self`, when the clock reaches that far back.
    pub fn checked_sub(self, by: Duration) -> Option<Self> {
        self.0.checked_sub(by).map(Self)
    }
}

impl Add<Duration> for UiInstant {
    type Output = Self;

    fn add(self, by: Duration) -> Self {
        Self(self.0 + by)
    }
}

/// Now for what the user perceives: expiries, debounces, grace periods, double presses, elapsed times, animations.
pub fn now() -> UiInstant {
    if *FROZEN {
        UiInstant(*START + Duration::from_millis(ADVANCED_MS.load(Ordering::SeqCst)))
    } else {
        UiInstant(real_now())
    }
}

/// UI time elapsed since `at`.
pub fn since(at: UiInstant) -> Duration {
    now().duration_since(at)
}

/// Now for timeouts, pacing, coalescing windows and metrics, which follow the machine.
#[allow(clippy::disallowed_methods)]
pub fn real_now() -> Instant {
    Instant::now()
}

/// Move frozen UI time forward; the harness's advance key.
pub fn advance(by: Duration) {
    ADVANCED_MS.fetch_add(by.as_millis() as u64, Ordering::SeqCst);
    ADVANCED.notify_waiters();
}

/// Sleep until `at` on the UI clock; forever when `None`.
pub async fn sleep_until(at: Option<UiInstant>) {
    let Some(at) = at else {
        return std::future::pending().await;
    };
    if !*FROZEN {
        return sleep_until_real(Some(at.0)).await;
    }
    loop {
        let advanced = ADVANCED.notified();
        tokio::pin!(advanced);
        advanced.as_mut().enable();
        if now() >= at {
            return;
        }
        advanced.await;
    }
}

/// Sleep until `at` on the machine clock; forever when `None`.
#[allow(clippy::disallowed_methods)]
pub async fn sleep_until_real(at: Option<Instant>) {
    match at {
        Some(at) => tokio::time::sleep_until(at.into()).await,
        None => std::future::pending().await,
    }
}

/// Sleep `duration` on the machine clock.
#[allow(clippy::disallowed_methods)]
pub async fn sleep_real(duration: Duration) {
    tokio::time::sleep(duration).await;
}

/// A skipping, drift-free ticker on the machine clock, for pacing and polling.
#[allow(clippy::disallowed_methods)]
pub fn interval_real(period: Duration) -> tokio::time::Interval {
    let mut interval = tokio::time::interval(period);
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    interval
}

/// A skipping ticker on the UI clock, for animations: under replay it ticks only as scenarios advance time.
pub struct Interval {
    period: Duration,
    next: UiInstant,
}

/// The first tick is one `period` away, so replay starts every animation on its first frame.
pub fn interval(period: Duration) -> Interval {
    Interval {
        period,
        next: now() + period,
    }
}

impl Interval {
    /// Wait for the next tick; ticks missed meanwhile are skipped.
    pub async fn tick(&mut self) {
        sleep_until(Some(self.next)).await;
        let now = now();
        self.next = self.next + self.period;
        if self.next <= now {
            self.next = now + self.period;
        }
    }

    /// Whether a tick is due but not yet taken.
    pub fn is_due(&self) -> bool {
        now() >= self.next
    }

    /// Restart the period from now.
    pub fn reset(&mut self) {
        self.next = now() + self.period;
    }
}
