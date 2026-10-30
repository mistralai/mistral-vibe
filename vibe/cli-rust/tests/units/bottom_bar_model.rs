//! The bottom bar names the active model before the token counter and elides it on narrow rows.

use vibe_rs::app::{App, Status};
use vibe_rs::input::handle_priority_key;
use vibe_rs::ui::bottom_bar::{self, split_room};

use crate::support::{bottom_bar, bottom_bar_row, ctrl, row, stub};

const COUNTER: &str = "0/600k tokens (0%)";
/// The ` · ` between the model and a shown counter.
const SEP: usize = 3;

fn app_with_model(model: &str, max_tokens: u64) -> App {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, max_tokens);
    app.session.startup_config.active_model = model.into();
    app
}

#[test]
fn the_model_shows_its_thinking_level_muted_like_the_rest_of_the_bar() {
    let mut app = app_with_model("mistral-medium-3.5[high]", 600_000);
    let buffer = bottom_bar(&mut app, 120);
    let row = row(&buffer, 0);
    let at = |text: &str| buffer[(row.find(text).expect("text") as u16, 0)].style();

    assert!(row.ends_with(&format!(" mistral-medium-3.5[high] · {COUNTER}")));
    assert_eq!(at("mistral"), at("0/600k"));
    assert_eq!(at("[high]"), at("/tmp/project"));
}

#[test]
fn the_active_model_sits_before_the_token_counter() {
    let mut app = app_with_model("mistral-medium-3.5", 600_000);

    let row = bottom_bar_row(&mut app, 120);

    assert!(row.starts_with("/tmp/project "));
    assert!(!row.contains("PID"));
    assert!(row.ends_with(&format!(" mistral-medium-3.5 · {COUNTER}")));
}

#[test]
fn a_click_on_the_model_name_and_nowhere_else_may_open_the_picker() {
    let mut app = app_with_model("mistral-medium-3.5", 600_000);
    let row = bottom_bar_row(&mut app, 120);
    let start = row.find("mistral").expect("model") as u16;
    let end = start + "mistral-medium-3.5".len() as u16;

    assert!(bottom_bar::model_at(&app, (start, 0)));
    assert!(bottom_bar::model_at(&app, (end - 1, 0)));
    assert!(!bottom_bar::model_at(&app, (start - 1, 0)));
    assert!(!bottom_bar::model_at(&app, (end + 2, 0)), "the counter");
    assert!(!bottom_bar::model_at(&app, (2, 0)), "the cwd");
}

#[test]
fn the_model_name_is_not_clickable_over_an_open_bottom_app() {
    let mut app = app_with_model("mistral-medium-3.5", 600_000);
    let row = bottom_bar_row(&mut app, 120);
    let start = row.find("mistral").expect("model") as u16;

    app.model_picker.open = true;

    assert!(!bottom_bar::model_at(&app, (start, 0)));
}

#[test]
fn a_hidden_model_is_not_clickable() {
    let mut app = app_with_model("mistral-medium-3.5", 600_000);
    let row = bottom_bar_row(&mut app, 30);

    assert!(!row.contains("mistr"), "{row:?}");
    assert!((0..30).all(|x| !bottom_bar::model_at(&app, (x, 0))));
}

#[test]
fn without_a_model_the_counter_stands_alone() {
    let mut app = app_with_model("", 600_000);

    let row = bottom_bar_row(&mut app, 120);

    assert!(row.ends_with(&format!(" {COUNTER}")));
    assert!(!row.contains('·'));
}

#[test]
fn without_a_counter_the_model_stands_alone() {
    let mut app = app_with_model("mistral-medium-3.5", 0);

    let row = bottom_bar_row(&mut app, 120);

    assert!(row.ends_with(" mistral-medium-3.5"));
    assert!(!row.contains('·'));
}

#[test]
fn a_narrow_row_elides_the_model_and_keeps_the_counter_whole() {
    let mut app = app_with_model("mistral-medium-3.5", 600_000);
    let width = "/tmp/project".len() + 1 + "mistral-m…".chars().count() + 3;

    let row = bottom_bar_row(&mut app, (width + COUNTER.len()) as u16);

    assert_eq!(row, format!("/tmp/project mistral-m… · {COUNTER}"));
}

#[test]
fn a_pending_quit_hint_elides_the_model_and_keeps_the_counter_whole() {
    let mut app = app_with_model("mistral-medium-3.5", 600_000);
    let event = ctrl('c');
    assert_eq!(handle_priority_key(&mut app, &stub(), event), Some(false));

    let hint = "Press Ctrl+C again to quit";
    let width = hint.len() + 1 + "mistral-m…".chars().count() + 3 + COUNTER.len();

    let row = bottom_bar_row(&mut app, width as u16);

    assert_eq!(row, format!("{hint} mistral-m… · {COUNTER}"));
}

#[test]
fn everything_fits_untouched() {
    assert_eq!(split_room(60, 20, 18, SEP), (20, 18));
}

#[test]
fn the_model_elides_first_down_to_its_floor() {
    assert_eq!(split_room(40, 30, 18, SEP), (30, 7));
    assert_eq!(split_room(39, 30, 18, SEP), (30, 6));
}

#[test]
fn a_cwd_shorter_than_its_floor_stays_whole_while_the_model_elides() {
    assert_eq!(split_room(25, 12, 18, SEP), (12, 10));
}

#[test]
fn the_cwd_yields_down_to_its_floor_once_the_model_is_at_its_own() {
    assert_eq!(split_room(30, 30, 18, SEP), (21, 6));
    assert_eq!(split_room(25, 30, 18, SEP), (16, 6));
}

#[test]
fn the_model_hides_before_the_cwd_goes_below_its_floor() {
    assert_eq!(split_room(24, 30, 18, SEP), (24, 0));
}

#[test]
fn a_model_without_room_is_hidden_and_the_cwd_takes_it_all() {
    assert_eq!(split_room(8, 30, 18, SEP), (8, 0));
}

#[test]
fn no_model_leaves_the_whole_room_to_the_cwd() {
    assert_eq!(split_room(20, 30, 0, SEP), (20, 0));
}

#[test]
fn without_a_separator_the_model_keeps_its_cells() {
    assert_eq!(split_room(25, 30, 18, 0), (19, 6));
    assert_eq!(split_room(22, 30, 18, 0), (16, 6));
}

#[test]
fn without_a_counter_no_room_is_kept_for_the_separator() {
    let mut app = app_with_model("mistral-medium-3.5", 0);
    let width = "/tmp/project".len() + 1 + "mistral-medium-3.5".len();

    let row = bottom_bar_row(&mut app, width as u16);

    assert_eq!(row, "/tmp/project mistral-medium-3.5");
}
