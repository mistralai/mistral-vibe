//! Where the user is on the page, kept across opens: filters, sort, folds, search and highlight.

use std::collections::HashSet;

use super::Sort;
use crate::option_list::{MultiAction, OptionList, SingleAction};
use crate::search_field::Search;

/// The column the keys act on.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Column {
    Folders,
    #[default]
    Sessions,
}

#[derive(Default)]
pub struct Browse {
    pub focus: Column,
    /// The folder the sessions column lists, by group key; `None` is All.
    pub folder: Option<String>,
    /// The folders column: option 0 is All, then one per folder.
    pub folders: OptionList<SingleAction>,
    /// The highlighted row and the scroll.
    pub list: OptionList<MultiAction>,
    pub search: Search,
    pub sort: Sort,
    pub folder_only: bool,
    pub archived: bool,
    /// Sessions whose folder is gone from disk are listed too.
    pub gone: bool,
    /// Started-session families whose fold the user flipped.
    pub toggled: HashSet<String>,
    /// The session the highlight lands on once it is listed: the row left on
    /// close, or the session just opened.
    pub highlight: Option<String>,
}
