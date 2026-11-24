//! Following a task's output: failed reads, and a selection riding its text as pages trim the top.

use super::{apply, next_read, Page, Read};
use crate::app::App;
use crate::server::BackgroundProcessOutput;
use crate::tasks_panel::output::KEEP_BYTES;
use crate::tasks_panel::{find::Find, Output, OutputView, TasksView};

fn showing(first_read_done: bool) -> (App, std::sync::Arc<crate::server::Client>) {
    let mut app = App::default();
    let task =
        serde_json::json!({ "processId": "p", "command": "make watch", "status": "running" });
    app.session.background_tasks = vec![serde_json::from_value(task).unwrap()];
    app.rail.window = Some(crate::rail::Item::Tasks);
    let mut output = Output::new("process-0".to_owned(), "make watch".to_owned(), 1);
    output.loaded = first_read_done;
    app.tasks_panel.view = TasksView::Output(Box::new(OutputView {
        output,
        search: crate::search_field::Search::default(),
        find: Find::default(),
    }));
    (app, std::sync::Arc::new(crate::server::Client::stub()))
}

fn view(app: &mut App) -> &mut OutputView {
    app.tasks_panel.shown_mut().unwrap()
}

fn failed() -> Page {
    Page {
        generation: 1,
        from_end: false,
        read: None,
    }
}

fn read(bytes: &[u8], next_cursor: u64, bytes_available: u64) -> BackgroundProcessOutput {
    use base64::{engine::general_purpose::STANDARD, Engine};
    BackgroundProcessOutput {
        availability: "available".to_owned(),
        output_base64: STANDARD.encode(bytes),
        next_cursor,
        bytes_available,
        is_final: true,
    }
}

fn page(bytes: &[u8]) -> Page {
    Page {
        generation: 1,
        from_end: false,
        read: Some(read(bytes, 1, 1)),
    }
}

fn last_page(bytes: &[u8], next_cursor: u64) -> Page {
    Page {
        generation: 1,
        from_end: true,
        read: Some(read(bytes, next_cursor, next_cursor)),
    }
}

#[tokio::test]
async fn a_failed_follow_read_keeps_following() {
    let (mut app, client) = showing(true);
    apply(&mut app, &client, failed());
    assert!(!view(&mut app).output.unavailable);
}

#[tokio::test]
async fn a_failed_first_read_says_the_output_is_unavailable() {
    let (mut app, client) = showing(false);
    apply(&mut app, &client, failed());
    assert!(view(&mut app).output.unavailable);
}

fn select_rows(app: &mut App, anchor: i32, head: i32) {
    app.selection.region = Some(crate::app::Selection {
        owner: crate::selection::RegionId::RailWindow,
        anchor: (4, anchor),
        head: (20, head),
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: crate::selection::ScrollTarget::Output,
        table_cell: None,
        text: String::new(),
    });
}

#[tokio::test]
async fn a_new_page_keeps_the_selection_on_its_text() {
    let (mut app, client) = showing(true);
    let line = "x".repeat(99) + "\n";
    let full = line.repeat(KEEP_BYTES / 100 - 2);
    view(&mut app).append(full.as_bytes());
    view(&mut app).output.wrap(200);
    select_rows(&mut app, 15, 17);
    apply(&mut app, &client, page(b"a short new line\n"));
    let selection = app.selection.region.as_ref();
    let selection = selection.expect("a page that trims nothing keeps it");
    assert_eq!((selection.anchor.1, selection.head.1), (15, 17));
    apply(&mut app, &client, page(line.repeat(10).as_bytes()));
    let selection = app.selection.region.as_ref();
    let selection = selection.expect("trimmed rows above move it up");
    assert!(selection.anchor.1 < 15, "{:?}", selection.anchor);
    assert_eq!(selection.head.1 - selection.anchor.1, 2);
}

#[tokio::test]
async fn a_search_match_keeps_its_text_as_the_top_is_trimmed() {
    let (mut app, client) = showing(true);
    let line = "x".repeat(99) + "\n";
    view(&mut app).append(format!("err\n{}", line.repeat(KEEP_BYTES / 200)).as_bytes());
    view(&mut app).append(b"err again\n");
    let view = view(&mut app);
    view.find.set_query("err", &view.output);
    let current = view.find.step(false);
    apply(
        &mut app,
        &client,
        page(line.repeat(KEEP_BYTES / 200).as_bytes()),
    );
    let view = app.tasks_panel.shown().unwrap();
    assert_eq!(view.find.hits(), [current.unwrap()], "the first one went");
    assert_eq!(view.find.position(), (Some(1), 1));
}

#[tokio::test]
async fn a_last_page_starting_mid_output_shows_from_its_first_whole_line() {
    let (mut app, client) = showing(false);
    let tail = [&"€".as_bytes()[1..], b"ok\nnext line\n"].concat();
    apply(&mut app, &client, last_page(&tail, 200_000));
    assert_eq!(view(&mut app).output.lines(), ["next line"]);
}

#[tokio::test]
async fn a_jump_to_the_latest_output_drops_the_old_lines_and_their_matches() {
    let (mut app, client) = showing(true);
    view(&mut app).append(b"err old\n");
    let view = view(&mut app);
    view.find.set_query("err", &view.output);
    apply(&mut app, &client, last_page(b"err new\n", 8));
    let view = app.tasks_panel.shown().unwrap();
    assert_eq!(view.output.lines(), ["err new"]);
    assert_eq!(view.find.hits(), [(view.output.first_line(), 0)]);
}

#[test]
fn a_backlog_reads_on_at_once_and_one_past_the_cap_jumps_to_the_end() {
    let behind = |unread: u64| next_read(&read(b"", 1_000, 1_000 + unread));
    assert_eq!(behind(0), Read::Next { paced: true });
    assert_eq!(behind(64_000), Read::Next { paced: false });
    assert_eq!(behind(KEEP_BYTES as u64 + 1), Read::Latest);
}

#[tokio::test]
async fn a_last_page_whose_only_line_break_ends_it_keeps_that_line() {
    let (mut app, client) = showing(false);
    let tail = [&"x".repeat(50).into_bytes()[..], b"\n"].concat();
    apply(&mut app, &client, last_page(&tail, 200_000));
    assert_eq!(view(&mut app).output.lines(), ["x".repeat(50)]);
}
