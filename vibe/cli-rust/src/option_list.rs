//! Option lists: the highlight, viewport and hit map of every list widget, and a click rule fixed by the list's type.

mod rule;

use std::marker::PhantomData;

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;

use crate::ui::list_scroll;

pub use rule::{Click, ClickRule, MultiAction, SingleAction};

/// An option list whose clicks follow `R`.
#[derive(Debug, Default)]
pub struct OptionList<R: ClickRule> {
    /// Highlighted option.
    selected: usize,
    /// First visible line of the viewport.
    scroll: usize,
    /// The wheel or scrollbar moved the viewport away from the highlight.
    free_scroll: bool,
    /// Painted rectangle of every visible option line, from the last layout.
    hits: Vec<(Rect, usize)>,
    /// Pressed option and whether it was highlighted at press time.
    pressed: Option<(usize, bool)>,
    rule: PhantomData<R>,
}

impl<R: ClickRule> OptionList<R> {
    pub fn new(selected: usize) -> Self {
        Self {
            selected,
            scroll: 0,
            free_scroll: false,
            hits: Vec::new(),
            pressed: None,
            rule: PhantomData,
        }
    }

    pub fn selected(&self) -> usize {
        self.selected
    }

    pub fn scroll(&self) -> usize {
        self.scroll
    }

    pub fn free_scroll(&self) -> bool {
        self.free_scroll
    }

    /// Highlight `index` and let the viewport follow it again.
    pub fn select(&mut self, index: usize) {
        self.highlight(index);
        self.free_scroll = false;
    }

    /// Highlight `index` without reattaching a free-scrolled viewport; only a
    /// moved highlight disarms, so a list rebuild keeps a pending confirm.
    pub fn highlight(&mut self, index: usize) {
        if self.selected != index {
            self.disarm();
        }
        self.selected = index;
    }

    /// Let the viewport follow the highlight again.
    pub fn follow(&mut self) {
        self.free_scroll = false;
    }

    /// Viewport back at the top, following the highlight.
    pub fn reset_viewport(&mut self) {
        self.scroll = 0;
        self.follow();
    }

    /// Back to the first option with the viewport at the top.
    pub fn reset(&mut self) {
        self.select(0);
        self.scroll = 0;
    }

    /// Highlight the next selectable option of `len`; false when none is left that way.
    pub fn step(
        &mut self,
        down: bool,
        len: usize,
        wrap: bool,
        selectable: impl Fn(usize) -> bool,
    ) -> bool {
        self.free_scroll = false;
        let len = len as isize;
        for offset in 1..=len {
            let mut index = self.selected as isize + if down { offset } else { -offset };
            if wrap {
                index = index.rem_euclid(len);
            } else if !(0..len).contains(&index) {
                return false;
            }
            if selectable(index as usize) {
                self.highlight(index as usize);
                return true;
            }
        }
        false
    }

    /// Scroll the viewport without moving the highlight.
    pub fn wheel(&mut self, down: bool, step: usize) {
        let scroll = if down {
            self.scroll.saturating_add(step)
        } else {
            self.scroll.saturating_sub(step)
        };
        self.detach_at(scroll);
    }

    /// Pin the viewport at `scroll` until the highlight moves again.
    pub fn detach_at(&mut self, scroll: usize) {
        self.scroll = scroll;
        self.free_scroll = true;
    }

    /// Lay out `lines` (the option on each content line, `None` for headers) in
    /// `area`: keep the highlight visible, record the hits, return the scroll.
    pub fn layout(&mut self, lines: &[Option<usize>], area: Rect) -> usize {
        let visible = usize::from(area.height);
        let scroll = if self.free_scroll {
            self.scroll.min(lines.len().saturating_sub(visible))
        } else {
            let first = lines.iter().position(|line| *line == Some(self.selected));
            let last = lines.iter().rposition(|line| *line == Some(self.selected));
            let highlight = first
                .zip(last)
                .map_or(0..0, |(first, last)| first..last + 1);
            list_scroll::follow(self.scroll, visible, lines.len(), highlight, |line| {
                lines[line].is_some()
            })
        };
        self.scroll = scroll;
        self.hits = (0..visible)
            .filter_map(|row| {
                let option = (*lines.get(scroll + row)?)?;
                let y = area.y + row as u16;
                Some((
                    Rect {
                        y,
                        height: 1,
                        ..area
                    },
                    option,
                ))
            })
            .collect();
        scroll
    }

    /// `layout` for a list drawing one line per option.
    pub fn layout_rows(&mut self, len: usize, area: Rect) -> usize {
        self.layout(&(0..len).map(Some).collect::<Vec<_>>(), area)
    }

    pub fn index_at(&self, at: (u16, u16)) -> Option<usize> {
        self.hits
            .iter()
            .find(|(area, _)| area.contains(at.into()))
            .map(|(_, option)| *option)
    }

    /// Painted rectangles of `option`'s visible lines.
    pub fn rows_of(&self, option: usize) -> impl Iterator<Item = Rect> + '_ {
        self.hits
            .iter()
            .filter(move |(_, hit)| *hit == option)
            .map(|(area, _)| *area)
    }

    /// Remember the option under the press and whether it was already highlighted.
    pub fn press(&mut self, at: (u16, u16)) -> Option<usize> {
        let option = self.index_at(at);
        self.pressed = option.map(|option| (option, option == self.selected));
        option
    }

    /// Make the pending press only highlight, when the highlight was not shown.
    fn disarm(&mut self) {
        if let Some((_, armed)) = &mut self.pressed {
            *armed = false;
        }
    }

    pub fn cancel(&mut self) {
        self.pressed = None;
    }

    /// A release on the pressed option highlights it and returns its click;
    /// a release elsewhere cancels.
    pub fn release(&mut self, at: (u16, u16)) -> Option<R::Click> {
        let (pressed, armed) = self.pressed.take()?;
        if self.index_at(at) != Some(pressed) {
            return None;
        }
        let was_highlighted = armed && pressed == self.selected;
        self.selected = pressed;
        Some(R::click(pressed, was_highlighted))
    }

    /// Route a left press or release; returns the click it completes.
    pub fn mouse(&mut self, event: MouseEvent) -> Option<R::Click> {
        let at = (event.column, event.row);
        match event.kind {
            MouseEventKind::Down(MouseButton::Left) => {
                self.press(at);
                None
            }
            MouseEventKind::Up(MouseButton::Left) => self.release(at),
            _ => None,
        }
    }
}

impl OptionList<MultiAction> {
    /// The option a completed click confirms.
    pub fn confirmed(&mut self, event: MouseEvent) -> Option<usize> {
        match self.mouse(event)? {
            Click::Confirm(option) => Some(option),
            Click::Highlight(_) => None,
        }
    }
}

/// Any option list, for the hover, wheel and scrollbar shared by all of them.
pub trait OptionView {
    fn selected(&self) -> usize;
    fn index_at(&self, at: (u16, u16)) -> Option<usize>;
    fn rows_of(&self, option: usize) -> Vec<Rect>;
    fn wheel(&mut self, down: bool, step: usize);
    fn detach_at(&mut self, scroll: usize);
}

impl<R: ClickRule> OptionView for OptionList<R> {
    fn selected(&self) -> usize {
        self.selected
    }

    fn index_at(&self, at: (u16, u16)) -> Option<usize> {
        OptionList::index_at(self, at)
    }

    fn rows_of(&self, option: usize) -> Vec<Rect> {
        OptionList::rows_of(self, option).collect()
    }

    fn wheel(&mut self, down: bool, step: usize) {
        OptionList::wheel(self, down, step);
    }

    fn detach_at(&mut self, scroll: usize) {
        OptionList::detach_at(self, scroll);
    }
}
