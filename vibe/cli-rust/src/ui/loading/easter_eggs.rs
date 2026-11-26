//! Labels that now and then stand in for `Generating` and `Thinking`.

use std::sync::OnceLock;

use super::{DEFAULT_LOADING_STATUS, THINKING_LOADING_STATUS};
use crate::ui::rng::Rng;

const PROBABILITY: f64 = 0.10;

const ALWAYS: &[&str] = &[
    "Eating a chocolatine",
    "Eating a pain au chocolat",
    "Réflexion",
    "Analyse",
    "Contemplation",
    "Synthèse",
    "Reading Proust",
    "Oui oui baguette",
    "Counting Rs in strawberry",
    "Seeding Mistral weights",
    "Vibing",
    "Sending good vibes",
    "Petting le chat",
];

const HALLOWEEN: &[&str] = &[
    "Trick or treating",
    "Carving pumpkins",
    "Summoning spirits",
    "Brewing potions",
    "Haunting the terminal",
    "Petting le chat noir",
];

const DECEMBER: &[&str] = &[
    "Wrapping presents",
    "Decorating the tree",
    "Drinking hot chocolate",
    "Building snowmen",
    "Writing holiday cards",
];

/// Replay scenarios name the egg every generic label shows, so a golden can render one.
const REPLAY_EGG_ENV: &str = "VIBE_REPLAY_EASTER_EGG";

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Mode {
    /// Tests and replays without a scripted egg keep their labels.
    #[default]
    Off,
    Random,
    Forced(&'static str),
}

static MODE: OnceLock<Mode> = OnceLock::new();

pub fn init() {
    let mode = match crate::utils::is_replaying() {
        false => Mode::Random,
        true => std::env::var(REPLAY_EGG_ENV)
            .ok()
            .and_then(|name| known(&name))
            .map_or(Mode::Off, Mode::Forced),
    };
    let _ = MODE.set(mode);
}

pub(super) fn mode() -> Mode {
    MODE.get().copied().unwrap_or_default()
}

fn known(name: &str) -> Option<&'static str> {
    [ALWAYS, HALLOWEEN, DECEMBER]
        .concat()
        .into_iter()
        .find(|egg| *egg == name)
}

pub(super) fn available(month: u8, day: u8) -> Vec<&'static str> {
    let mut eggs = ALWAYS.to_vec();
    if (month, day) == (10, 31) {
        eggs.extend_from_slice(HALLOWEEN);
    }
    if month == 12 {
        eggs.extend_from_slice(DECEMBER);
    }
    eggs
}

/// The egg `mode` shows for `label`; only the generic labels are replaced, the others carry information.
pub(super) fn hatch(mode: Mode, rng: &mut Rng, label: &str) -> Option<&'static str> {
    match mode {
        Mode::Off => None,
        Mode::Random => pick(rng, label, today),
        Mode::Forced(egg) => replaceable(label).then_some(egg),
    }
}

fn replaceable(label: &str) -> bool {
    label == DEFAULT_LOADING_STATUS || label == THINKING_LOADING_STATUS
}

/// A random egg for `label`, the date read from `today` only on a hit.
pub(super) fn pick(
    rng: &mut Rng,
    label: &str,
    today: impl FnOnce() -> (u8, u8),
) -> Option<&'static str> {
    if !replaceable(label) {
        return None;
    }
    if rng.random() >= PROBABILITY {
        return None;
    }
    let (month, day) = today();
    let eggs = available(month, day);
    Some(eggs[(rng.next_u64() % eggs.len() as u64) as usize])
}

fn today() -> (u8, u8) {
    crate::utils::datetime::local_month_day(crate::utils::now_ms())
}
