//! Horizontal trust margins map to text without changing vertical selection ownership.

use ratatui::layout::Rect;
use vibe_rs::app::App;
use vibe_rs::selection::{Region, ScrollTarget};
use vibe_rs::trust_folders::selection_position;

fn app_with_scroll_region() -> App {
    let mut app = App::default();
    app.view.selection_region = Region {
        area: Rect::new(20, 5, 60, 12),
        scroll_area: Rect::new(20, 6, 59, 4),
        scroll_target: ScrollTarget::Trust,
        scrollbar: true,
        ..Default::default()
    };
    app
}

#[test]
fn side_margins_anchor_in_the_scroll_zone_not_its_scrollbar() {
    let app = app_with_scroll_region();
    for (x, expected) in [(0, 20), (19, 20), (20, 20), (78, 78), (80, 78), (119, 78)] {
        let at = selection_position(&app, (x, 7));
        assert_eq!(at, (expected, 7));
        assert!(app.view.selection_region.contains(at));
        assert!(app.view.selection_region.scroll_target_at(at) == ScrollTarget::Trust);
    }
    let scrollbar = selection_position(&app, (79, 7));
    assert_eq!(scrollbar, (79, 7));
    assert!(!app.view.selection_region.contains(scrollbar));
}

#[test]
fn footer_and_top_padding_keep_their_original_vertical_zone() {
    let app = app_with_scroll_region();
    for y in [5, 10, 12, 16] {
        for (x, expected) in [(0, 20), (19, 20), (79, 79), (80, 79), (119, 79)] {
            let at = selection_position(&app, (x, y));
            assert_eq!(at, (expected, y));
            assert!(app.view.selection_region.scroll_target_at(at) == ScrollTarget::None);
        }
    }
    for y in [0, 4, 17, 39] {
        let at = selection_position(&app, (0, y));
        assert!(!app.view.selection_region.contains(at));
    }
}

#[test]
fn margins_work_without_overflow_and_empty_dialogs_stay_unselectable() {
    let mut app = App::default();
    assert_eq!(selection_position(&app, (100, 8)), (100, 8));
    app.view.selection_region.area = Rect::new(20, 5, 60, 12);
    assert_eq!(selection_position(&app, (0, 8)), (20, 8));
    assert_eq!(selection_position(&app, (100, 8)), (79, 8));
}
