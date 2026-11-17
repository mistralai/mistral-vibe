//! The animated braille cat: one transition round per start, replayed on click.

mod frames;

use std::collections::HashSet;
use std::time::Duration;

use frames::{LECHONK_STARTING_DOTS, LECHONK_TRANSITIONS, STARTING_DOTS, TRANSITIONS};

use crate::ui::braille_renderer;

const WIDTH: i16 = 22;
const HEIGHT: i16 = 12;
const LECHONK_WIDTH: i16 = 26;
const LECHONK_HEIGHT: i16 = 12;
pub const FRAME_INTERVAL: Duration = Duration::from_millis(160);

/// Which cat sprite to animate.
#[derive(Clone, Copy, Default, PartialEq, Eq)]
pub enum CatVariant {
    /// The classic petit chat (22×12).
    #[default]
    LeChat,
    /// The chonkier pixilart cat (26×12), shown for Mistral Large / ml4 models.
    LeChonk,
}

impl CatVariant {
    /// Pick the variant from a model display name or alias.
    /// "ml4", "mistral large" (or any "mistral-large-*" API alias),
    /// "le-chaton-fat", "le-gros-chaton", "le-chonk" → LeChonk.
    /// Anything else → LeChat.
    pub fn from_model(model: &str) -> Self {
        let lower = model.to_lowercase();
        // Normalize hyphens to spaces so "mistral large" catches both
        // "Mistral Large" and "mistral-large-latest" / "mistral-large-4" etc.
        let normalized = lower.replace('-', " ");
        if normalized.contains("ml4")
            || normalized.contains("mistral large")
            || lower.contains("le-chaton-fat")
            || lower.contains("le-gros-chaton")
            || lower.contains("le-chonk")
        {
            CatVariant::LeChonk
        } else {
            CatVariant::LeChat
        }
    }

    fn starting_dots(&self) -> &'static [&'static [i16]] {
        match self {
            CatVariant::LeChat => &STARTING_DOTS,
            CatVariant::LeChonk => &LECHONK_STARTING_DOTS,
        }
    }

    fn transitions(&self) -> &'static [frames::Transition] {
        match self {
            CatVariant::LeChat => TRANSITIONS,
            CatVariant::LeChonk => LECHONK_TRANSITIONS,
        }
    }

    fn width(&self) -> i16 {
        match self {
            CatVariant::LeChat => WIDTH,
            CatVariant::LeChonk => LECHONK_WIDTH,
        }
    }

    fn height(&self) -> i16 {
        match self {
            CatVariant::LeChat => HEIGHT,
            CatVariant::LeChonk => LECHONK_HEIGHT,
        }
    }
}

/// The animated cat state: one round plays from the starting pose back to it.
pub struct PetitChat {
    dots: HashSet<(i16, i16)>,
    transition_index: usize,
    /// Whether a round is playing; a finished round rests until restarted.
    animating: bool,
    variant: CatVariant,
}

impl Default for PetitChat {
    fn default() -> Self {
        Self::at_start(CatVariant::LeChat)
    }
}

impl PetitChat {
    /// Switch to a different cat variant, replaying its round.
    pub fn set_variant(&mut self, variant: CatVariant) {
        if self.variant == variant {
            return;
        }
        *self = Self::at_start(variant);
    }

    /// Replay a full round, e.g. on a click; one already playing is left to finish.
    pub fn restart(&mut self) {
        self.animating = true;
    }

    /// Advance one frame; a rest skips the tick. Returns whether the dots changed.
    pub fn tick(&mut self) -> bool {
        if !self.animating {
            return false;
        }
        let transitions = self.variant.transitions();
        let transition = &transitions[self.transition_index];
        let mut changed = false;
        for dot in transition.remove {
            changed |= self.dots.remove(dot);
        }
        for &dot in transition.add {
            changed |= self.dots.insert(dot);
        }
        self.transition_index = (self.transition_index + 1) % transitions.len();
        self.animating = self.transition_index != 0;
        changed
    }

    /// The current frame as a braille grid (`\n`-joined rows).
    pub fn render(&self) -> String {
        braille_renderer::render_braille(&self.dots, self.variant.width(), self.variant.height())
    }

    /// The cat set to `variant`'s starting pose, with its round ready to play.
    fn at_start(variant: CatVariant) -> Self {
        let mut dots = HashSet::new();
        for (y, row) in variant.starting_dots().iter().enumerate() {
            for &x in *row {
                dots.insert((x, y as i16));
            }
        }
        Self {
            dots,
            transition_index: 0,
            animating: true,
            variant,
        }
    }
}
