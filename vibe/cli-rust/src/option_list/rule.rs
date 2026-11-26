//! Click rules: what a click on an option does, from the keys that act on it.

/// A list's click rule. Count the keys that act on the highlighted option;
/// navigation, search, Esc and whole-list keys (`r` refresh) do not count.
pub trait ClickRule {
    /// What a completed click yields.
    type Click;
    fn click(option: usize, was_highlighted: bool) -> Self::Click;
}

/// Choosing is the option's only action: one click confirms it.
#[derive(Debug, Default)]
pub struct SingleAction;

/// The option has several actions (`/model`'s Enter and `s`): a click
/// highlights it, a click on the highlight confirms it.
#[derive(Debug, Default)]
pub struct MultiAction;

/// What a click on a `MultiAction` option does.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Click {
    Highlight(usize),
    Confirm(usize),
}

impl ClickRule for SingleAction {
    type Click = usize;

    fn click(option: usize, _: bool) -> usize {
        option
    }
}

impl ClickRule for MultiAction {
    type Click = Click;

    fn click(option: usize, was_highlighted: bool) -> Click {
        if was_highlighted {
            Click::Confirm(option)
        } else {
            Click::Highlight(option)
        }
    }
}
