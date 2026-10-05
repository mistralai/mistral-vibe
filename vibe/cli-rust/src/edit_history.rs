//! Bounded composer checkpoints, with Textual-style edit batching.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

use crate::app::ChatInput;
use crate::input_modes::InputMode;

pub const MAX_CHECKPOINTS: usize = 50;
pub const MAX_HISTORY_BYTES: usize = 4 * 1024 * 1024;
const MAX_BATCH_CHARACTERS: usize = 100;
const CHECKPOINT_TIMER: Duration = Duration::from_secs(2);

#[derive(Clone, PartialEq, Eq)]
pub struct Snapshot {
    text: String,
    cursor: usize,
    anchor: Option<usize>,
    mode: InputMode,
    mentions: Vec<crate::mentions::Mention>,
    generation: u64,
}

impl Snapshot {
    pub fn capture(input: &ChatInput) -> Self {
        // An edit not reconciled yet (an appended dictation) still keeps its mentions.
        let mentions = match input.mentions.synced(&input.input) {
            true => input.mentions.spans(&input.input).to_vec(),
            false => {
                let mut mentions = input.mentions.clone();
                mentions.sync(&input.input);
                mentions.spans(&input.input).to_vec()
            }
        };
        Self {
            text: input.input.clone(),
            cursor: input.cursor,
            anchor: input.anchor,
            mode: input.mode,
            mentions,
            generation: input.edit_history.generation,
        }
    }

    /// The collapsed pastes this checkpoint holds, shared with other checkpoints.
    fn pastes(&self) -> impl Iterator<Item = &std::sync::Arc<str>> {
        self.mentions
            .iter()
            .filter_map(|mention| mention.paste.as_ref())
    }

    fn same_text(&self, other: &Self) -> bool {
        self.text == other.text && self.mode == other.mode
    }

    pub fn restore(self, input: &mut ChatInput) {
        input.input = self.text;
        input.mentions.restore(&input.input, self.mentions);
        input.cursor = self.cursor;
        input.anchor = self.anchor;
        input.mode = self.mode;
        input.scroll = None;
        input.normalize_positions();
    }
}

struct Batch {
    before: Snapshot,
    after: Snapshot,
}

impl Batch {
    fn text_bytes(&self) -> usize {
        self.before.text.len() + self.after.text.len()
    }
}

/// Bytes the checkpoints hold: every text, plus each distinct collapsed paste
/// once, however many checkpoints share it.
fn history_bytes(batches: &VecDeque<Batch>) -> usize {
    let mut seen = std::collections::HashSet::new();
    let pastes: usize = batches
        .iter()
        .flat_map(|batch| batch.before.pastes().chain(batch.after.pastes()))
        .filter(|paste| seen.insert(std::sync::Arc::as_ptr(paste).cast::<u8>()))
        .map(|paste| paste.len())
        .sum();
    batches.iter().map(Batch::text_bytes).sum::<usize>() + pastes
}

#[derive(Default)]
pub struct EditHistory {
    undo: VecDeque<Batch>,
    redo: Vec<Batch>,
    generation: u64,
    last_edit: Option<Instant>,
    characters: usize,
    replaced: bool,
}

impl EditHistory {
    pub fn clear(&mut self) {
        self.undo.clear();
        self.redo.clear();
        self.generation = self.generation.wrapping_add(1);
        self.checkpoint();
    }

    pub fn checkpoint(&mut self) {
        self.last_edit = None;
    }

    pub fn record(&mut self, before: Snapshot, after: Snapshot, isolated: bool, now: Instant) {
        if before.generation != self.generation || before.same_text(&after) {
            return;
        }
        let (removed, inserted) = changed_text(&before.text, &after.text);
        let characters = inserted.chars().count() + removed.chars().count();
        let replaced = !removed.is_empty();
        let isolated = isolated
            || inserted.chars().count() > 1
            || removed.contains('\n')
            || inserted.contains('\n')
            || before.mode != after.mode;
        let merge = !isolated
            && self
                .last_edit
                .is_some_and(|last| now.saturating_duration_since(last) <= CHECKPOINT_TIMER)
            && self.replaced == replaced
            && self.characters + characters <= MAX_BATCH_CHARACTERS
            && self.undo.back().is_some_and(|batch| batch.after == before);
        self.redo.clear();
        if merge {
            self.undo.back_mut().unwrap().after = after;
            self.characters += characters;
        } else {
            self.undo.push_back(Batch { before, after });
            self.characters = characters;
        }
        self.replaced = replaced;
        self.last_edit = (!isolated).then_some(now);
        // Evict oldest checkpoints; an oversized edit becomes a non-undoable baseline.
        while self.undo.len() > MAX_CHECKPOINTS || history_bytes(&self.undo) > MAX_HISTORY_BYTES {
            self.undo.pop_front();
        }
    }

    pub fn restore(&mut self, current: &Snapshot, redo: bool) -> Option<Snapshot> {
        self.checkpoint();
        let batch = if redo {
            self.redo.last()?
        } else {
            self.undo.back()?
        };
        let expected = if redo { &batch.before } else { &batch.after };
        if !expected.same_text(current) {
            self.clear();
            return None;
        }
        if redo {
            let batch = self.redo.pop()?;
            let snapshot = batch.after.clone();
            self.undo.push_back(batch);
            Some(snapshot)
        } else {
            let batch = self.undo.pop_back()?;
            let snapshot = batch.before.clone();
            self.redo.push(batch);
            Some(snapshot)
        }
    }
}

fn changed_text<'a>(before: &'a str, after: &'a str) -> (&'a str, &'a str) {
    let prefix: usize = before
        .chars()
        .zip(after.chars())
        .take_while(|(a, b)| a == b)
        .map(|(ch, _)| ch.len_utf8())
        .sum();
    let before = &before[prefix..];
    let after = &after[prefix..];
    let suffix: usize = before
        .chars()
        .rev()
        .zip(after.chars().rev())
        .take_while(|(a, b)| a == b)
        .map(|(ch, _)| ch.len_utf8())
        .sum();
    (
        &before[..before.len() - suffix],
        &after[..after.len() - suffix],
    )
}

impl ChatInput {
    pub fn record_edit(&mut self, before: Snapshot, isolated: bool, now: Instant) {
        let after = Snapshot::capture(self);
        self.edit_history.record(before, after, isolated, now);
    }

    pub fn restore_edit(&mut self, redo: bool) -> bool {
        let current = Snapshot::capture(self);
        let Some(snapshot) = self.edit_history.restore(&current, redo) else {
            return false;
        };
        snapshot.restore(self);
        true
    }
}
