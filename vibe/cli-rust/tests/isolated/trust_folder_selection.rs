//! Trust-folder text-selection behavior.

use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::Terminal;

use vibe_rs::app::{App, Selection};
use vibe_rs::selection::{self, region, ScrollTarget};
use vibe_rs::server::WorkspaceTrustDetails;
use vibe_rs::ui;

#[test]
fn edge_drag_scrolls_the_detected_files_and_extends_the_selection() {
    let area = Rect::new(20, 5, 60, 10);
    let mut app = App::default();
    app.trust.scroll_max = 20;
    app.view.selection_region.area = area;
    app.view.selection_region.scroll_area = area;
    app.view.selection_region.scroll_target = ScrollTarget::Trust;
    app.view.selection_scrollbar.update(
        Rect::new(area.right() - 1, area.y, 1, area.height),
        30,
        area.height,
        0,
    );

    selection::press(&mut app, (30, 10));
    selection::drag(&mut app, (30, area.bottom() - 1));

    assert_eq!(app.selection.region.as_ref().unwrap().edge_scroll, 3);
    let old_head = app.selection.region.as_ref().unwrap().head;
    assert!(selection::auto_scroll(&mut app));
    assert_eq!(app.trust.scroll, 3);
    assert_eq!(
        app.selection.region.as_ref().unwrap().head,
        (old_head.0, old_head.1 + 3)
    );

    selection::drag(&mut app, (30, area.y));
    assert_eq!(app.selection.region.as_ref().unwrap().edge_scroll, -3);
    let old_head = app.selection.region.as_ref().unwrap().head;
    assert!(selection::auto_scroll(&mut app));
    assert_eq!(app.trust.scroll, 0);
    assert_eq!(
        app.selection.region.as_ref().unwrap().head,
        (old_head.0, old_head.1 - 3)
    );
}

#[test]
fn upward_scrolling_selection_does_not_highlight_the_fixed_footer() {
    let area = Rect::new(20, 5, 60, 10);
    let scroll_area = Rect::new(20, 5, 60, 6);
    let mut buffer = Buffer::empty(area);
    for y in area.y..area.bottom() {
        buffer.set_string(area.x, y, "selectable text", Style::default());
    }
    let mut app = App::default();
    app.view.selection_region.area = area;
    app.view.selection_region.scroll_area = scroll_area;
    app.view.selection_region.scroll_target = ScrollTarget::Trust;
    app.view.selection_region.top = i32::from(area.y);
    app.selection.region = Some(Selection {
        owner: region::RegionId::Main,
        anchor: (30, 9),
        head: (30, 5),
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: ScrollTarget::Trust,
        table_cell: None,
        text: String::new(),
    });

    let spans = region::spans(&app, &buffer, area);

    assert!(!spans.is_empty());
    assert!(spans.iter().all(|(y, _, _)| *y < scroll_area.bottom()));
}

#[test]
fn fixed_footer_selection_does_not_move_with_the_file_list() {
    let area = Rect::new(20, 5, 60, 10);
    let scroll_area = Rect::new(20, 5, 60, 6);
    let mut buffer = Buffer::empty(area);
    buffer.set_string(20, 12, "selectable fixed footer text", Style::default());
    let mut app = App::default();
    app.view.selection_region.area = area;
    app.view.selection_region.scroll_area = scroll_area;
    app.view.selection_region.scroll_target = ScrollTarget::Trust;
    app.view.selection_region.top = i32::from(area.y) - 2;

    selection::press_including_padding(&mut app, (30, 12));
    assert!(app.selection.region.as_ref().unwrap().scroll_target == ScrollTarget::None);
    app.view.selection_region.top -= 3;
    selection::drag(&mut app, (40, 12));

    let spans = region::spans(&app, &buffer, area);
    assert!(!spans.is_empty());
    assert!(spans.iter().all(|(y, _, _)| *y == 12));
}

#[test]
fn short_dialog_clamps_the_scroll_area_to_painted_rows() {
    let mut app = App::default();
    app.trust.open = true;
    app.trust.details = Some(WorkspaceTrustDetails {
        cwd: "/home/user/untrusted".to_owned(),
        repo_root: Some("/home/user".to_owned()),
        detected_files: (0..20).map(|index| format!("file{index:02}.md")).collect(),
        repo_detected_files: Vec::new(),
        repo_explicitly_untrusted: false,
        settings_path: "/home/user/.vibe/trusted_folders.toml".to_owned(),
        available_decisions: vec!["trust_repo".to_owned(), "trust_cwd".to_owned()],
    });
    app.trust.scroll = usize::MAX;
    let mut terminal = Terminal::new(TestBackend::new(80, 8)).expect("terminal");

    terminal
        .draw(|frame| ui::draw(&mut app, frame))
        .expect("draw");

    let region = app.view.selection_region;
    assert!(region.scroll_area.bottom() <= region.area.bottom());
    assert_eq!(
        app.trust.scroll_max,
        app.view.selection_scrollbar.max_scroll_large().unwrap()
    );
    assert_eq!(app.trust.scroll, app.trust.scroll_max);
}

#[test]
fn fixed_footer_selection_keeps_the_last_column_in_both_directions() {
    let area = Rect::new(20, 5, 10, 6);
    let mut buffer = Buffer::empty(area);
    buffer.set_string(20, 7, "/abcdefghi", Style::default());
    buffer.set_string(20, 8, "/jklmnopqr", Style::default());
    for (start, end) in [((20, 7), (29, 8)), ((29, 8), (20, 7))] {
        let mut app = App::default();
        app.view.selection_region.area = area;
        app.view.selection_region.scroll_area = Rect::new(20, 5, 9, 2);
        app.view.selection_region.scroll_target = ScrollTarget::Trust;
        app.view.selection_region.scrollbar = true;
        app.view.selection_region.end_exclusive = true;

        selection::press_including_padding(&mut app, start);
        selection::drag(&mut app, end);

        let spans = region::spans(&app, &buffer, app.view.selection_region.content());
        assert_eq!(spans, vec![(7, 20, 29), (8, 20, 29)]);
        assert_eq!(region::extract(&buffer, &spans), "/abcdefghi\n/jklmnopqr");
    }
}

#[test]
fn trust_scrollbar_only_excludes_cells_in_the_scrolling_rows() {
    let region = region::Region {
        area: Rect::new(20, 5, 10, 6),
        scroll_area: Rect::new(20, 5, 9, 2),
        scroll_target: ScrollTarget::Trust,
        scrollbar: true,
        ..Default::default()
    };

    assert!(!region.contains((29, 5)));
    assert!(!region.contains((29, 6)));
    assert!(region.contains((29, 7)));
    assert!(region.contains((29, 10)));
    assert!(!region.contains((30, 7)));
}

#[test]
fn offscreen_copy_drops_centering_padding() {
    let mut app = App::default();
    app.trust.details = Some(WorkspaceTrustDetails {
        cwd: "/home/user/untrusted".to_owned(),
        repo_root: None,
        detected_files: vec!["file00.md".to_owned(), "file01.md".to_owned()],
        repo_detected_files: Vec::new(),
        repo_explicitly_untrusted: false,
        settings_path: "/home/user/.vibe/trusted_folders.toml".to_owned(),
        available_decisions: vec!["trust_cwd".to_owned(), "decline".to_owned()],
    });
    app.view.selection_region.area = Rect::new(20, 5, 60, 10);
    app.view.selection_region.scrollbar = true;
    app.view.selection_region.end_exclusive = true;
    app.selection.region = Some(Selection {
        owner: region::RegionId::Main,
        anchor: (20, 1),
        head: (78, 7),
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: ScrollTarget::Trust,
        table_cell: None,
        text: String::new(),
    });

    assert_eq!(
        region::extract_document(&app).unwrap(),
        "Malicious configs can modify AI behavior, exfiltrate data, \
         run destructive commands, or silently alter your code.\n\
         Detected in current folder:\n\
         • file00.md\n\
         • file01.md"
    );
}
