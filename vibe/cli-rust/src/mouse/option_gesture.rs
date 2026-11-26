//! Clicks on an option list drawn inside a selectable text region.

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};

use crate::app::App;
use crate::option_list::{ClickRule, OptionList};
use crate::selection::{self, RegionId, Release};

/// Route a left press, drag or release on `list` inside `owner`'s text: a press
/// on the highlighted option is a confirm click, never a double-click word
/// selection, and a drag selects text instead of clicking. A press that
/// started no text gesture clicks on any release over the same option.
pub fn option_gesture<R: ClickRule>(
    app: &mut App,
    event: MouseEvent,
    owner: RegionId,
    list: fn(&mut App) -> &mut OptionList<R>,
) -> Option<R::Click> {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            let options = list(app);
            if options.press(at) == Some(options.selected()) {
                app.selection.chain = Default::default();
            }
            selection::press_owned(app, at, owner);
            None
        }
        MouseEventKind::Drag(MouseButton::Left) => {
            selection::drag(app, at);
            None
        }
        MouseEventKind::Up(MouseButton::Left) => {
            let same_cell = app.selection.press.is_none_or(|press| press == at);
            let selected = matches!(selection::release(app), Release::Selected);
            if same_cell && !selected {
                return list(app).release(at);
            }
            list(app).cancel();
            None
        }
        _ => None,
    }
}
