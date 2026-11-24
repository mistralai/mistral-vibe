//! The Sessions page drawn at many sizes over hostile data: nothing may spill
//! out of the list, cross the rule into the preview, or overwrite it.

use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::Terminal;
use vibe_rs::app::{App, Status};
use vibe_rs::sessions_page::row::{Session, Tokens, Worktree};
use vibe_rs::sessions_page::{self, Trigger};

use crate::support::stub;

const LONG: &str =
    "mgesbert-vibe-5672-smart-approval-fails-with-reasoning-models-when-the-prompt-is-long";

fn session(index: u64, title: &str, cwd: &str) -> Session {
    Session {
        id: format!("session-{index:04}"),
        title: title.into(),
        cwd: Some(cwd.into()),
        repository_root: Some(cwd.into()),
        // The awkward sessions (small indices) are the newest, so they show first.
        created_at: 10_000 - index,
        updated_at: 10_000 - index,
        bumped_at: Some(10_000 - index),
        ..Session::default()
    }
}

/// Every awkward thing a real store holds: long folders, long titles, wide
/// characters, branches, deleted worktrees, started sessions.
fn hostile() -> Vec<Session> {
    let mut sessions = vec![
        session(1, "Short", "/w/dashboard"),
        session(2, &"Very long title ".repeat(12), "/w/dashboard"),
        session(
            3,
            "日本語のタイトルとても長いセッション名前です日本語のタイトル",
            &format!("/w/{LONG}"),
        ),
        session(4, "Emoji 🚀🚀🚀 title", &format!("/w/{LONG}-2")),
        session(5, "Deleted worktree", &format!("/w/{LONG}/vibe")),
        session(6, "Started", "/w/dashboard"),
        session(7, "x", "/"),
    ];
    sessions[1].worktree = Some(Worktree {
        name: "wt".into(),
        branch: format!("feature/{LONG}"),
    });
    sessions[4].repository_root = Some("/w/dashboard".into());
    sessions[4].checkout_root = Some(format!("/w/{LONG}"));
    sessions[4].cwd_missing = true;
    sessions[5].started_by_session_id = Some("session-0001".into());
    sessions[0].model = Some(LONG.into());
    sessions[0].reasoning_effort = Some("high".into());
    sessions[0].token_usage = Some(Tokens {
        input_tokens: 95_300,
        output_tokens: 781,
    });
    for index in 8..60 {
        sessions.push(session(
            index,
            &format!("Filler {index}"),
            &format!("/w/project-{}", index % 9),
        ));
    }
    sessions
}

fn page(sessions: Vec<Session>, help: bool) -> App {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.session.cwd = Some("/w/dashboard".into());
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    app.sessions_page.loading = false;
    app.sessions_page.sessions = sessions;
    app.sessions_page.help = help;
    sessions_page::relayout(&mut app);
    app
}

fn draw(app: &mut App, width: u16, height: u16) -> Buffer {
    let mut terminal = Terminal::new(TestBackend::new(width, height)).expect("terminal");
    terminal
        .draw(|frame| vibe_rs::ui::sessions_page::draw(app, frame, frame.area()))
        .expect("draw");
    terminal.backend().buffer().clone()
}

fn rows(buffer: &Buffer) -> Vec<String> {
    let area = buffer.area;
    (0..area.height)
        .map(|y| (0..area.width).map(|x| buffer[(x, y)].symbol()).collect())
        .collect()
}

/// The column of the rule between the list and the preview, if drawn: the last rule.
fn rule_column(buffer: &Buffer) -> Option<u16> {
    let area = buffer.area;
    (0..area.width)
        .rev()
        .find(|&x| (4..area.height.saturating_sub(4)).all(|y| buffer[(x, y)].symbol() == "│"))
}

const SIZES: &[(u16, u16)] = &[
    (20, 6),
    (30, 10),
    (60, 20),
    (80, 24),
    (99, 30),
    (100, 30),
    (101, 30),
    (120, 40),
    (160, 45),
    (220, 60),
];

#[test]
fn nothing_crosses_the_rule_into_the_preview() {
    for &(width, height) in SIZES {
        for help in [false, true] {
            let mut app = page(hostile(), help);
            // Walk the highlight down so every row, header and scroll offset is drawn.
            for step in 0..40 {
                let buffer = draw(&mut app, width, height);
                if let Some(rule) = rule_column(&buffer) {
                    let screen = rows(&buffer).join("\n");
                    // The column right before the rule is the gap after the
                    // scrollbar: list text there has spilled into the pane.
                    for y in 3..height.saturating_sub(4) {
                        let left = buffer[(rule - 1, y)].symbol();
                        assert!(
                            left.trim().is_empty(),
                            "{width}x{height} help={help} step={step}: text at the gap, row {y}\n{screen}"
                        );
                    }
                }
                vibe_rs::sessions_page::keys::handle_key(
                    &mut app,
                    &stub(),
                    crate::support::key(crossterm::event::KeyCode::Char('j')),
                );
            }
        }
    }
}

/// The pane paints every cell it owns: a shorter line never leaves the
/// list's text showing through after it.
#[test]
fn the_preview_owns_every_cell_right_of_the_rule() {
    for &(width, height) in SIZES {
        let mut app = page(hostile(), false);
        let buffer = draw(&mut app, width, height);
        let Some(rule) = rule_column(&buffer) else {
            continue;
        };
        let mut bare = page(hostile(), false);
        bare.sessions_page.sessions.truncate(1);
        sessions_page::relayout(&mut bare);
        let alone = draw(&mut bare, width, height);
        for y in 3..height.saturating_sub(3) {
            for x in rule + 1..width {
                assert_eq!(
                    buffer[(x, y)].symbol(),
                    alone[(x, y)].symbol(),
                    "{width}x{height}: pane cell ({x}, {y}) depends on the list\n{}",
                    rows(&buffer).join("\n")
                );
            }
        }
    }
}

#[test]
fn headers_and_rows_stay_on_their_line() {
    for &(width, height) in SIZES {
        let mut app = page(hostile(), false);
        let buffer = draw(&mut app, width, height);
        let screen = rows(&buffer);
        // The list holds no row wider than the frame, and a header never
        // runs into the preview.
        for line in &screen {
            assert!(
                !line.contains("Modelpython") && !line.contains(&format!("{LONG}{LONG}")),
                "{width}x{height}\n{}",
                screen.join("\n")
            );
        }
    }
}

/// Prints the page at a few sizes; run with `--nocapture` to eyeball it.
#[test]
#[ignore = "visual inspection"]
fn print_screens() {
    for &(width, height) in &[(40u16, 14u16), (80, 22), (110, 24), (160, 26)] {
        for help in [false, true] {
            let mut app = page(hostile(), help);
            for _ in 0..2 {
                vibe_rs::sessions_page::keys::handle_key(
                    &mut app,
                    &stub(),
                    crate::support::key(crossterm::event::KeyCode::Char('j')),
                );
            }
            let buffer = draw(&mut app, width, height);
            println!("===== {width}x{height} help={help}");
            for line in rows(&buffer) {
                println!("|{line}|");
            }
        }
    }
}

/// Walking the whole list down then up, at every size, the highlighted row
/// is drawn: the pinned header and the unfolded hints never hide it.
#[test]
fn the_highlighted_row_is_always_in_view() {
    for &(width, height) in SIZES {
        if height < 10 {
            continue;
        }
        for help in [false, true] {
            let mut app = page(hostile(), help);
            let total = app.sessions_page.view.rows.len();
            let keys = std::iter::repeat_n('j', total).chain(std::iter::repeat_n('k', total));
            for (step, key) in keys.enumerate() {
                let buffer = draw(&mut app, width, height);
                let screen = rows(&buffer);
                let label = sessions_page::selected(&app)
                    .expect("a row")
                    .label()
                    .to_owned();
                // A wide character takes two cells, the second one blank.
                let shown: String = label.chars().take(4).collect();
                let squeezed: Vec<String> =
                    screen.iter().map(|line| line.replace(' ', "")).collect();
                let wanted = shown.replace(' ', "");
                assert!(
                    squeezed.iter().any(|line| line.contains(&wanted)),
                    "{width}x{height} help={help} step={step}: `{label}` not drawn\n{}",
                    screen.join("\n")
                );
                vibe_rs::sessions_page::keys::handle_key(
                    &mut app,
                    &stub(),
                    crate::support::key(crossterm::event::KeyCode::Char(key)),
                );
            }
        }
    }
}

#[test]
fn a_narrow_footer_wraps_every_hint_and_the_cap_shows_in_the_title() {
    let mut app = page(hostile(), false);
    app.sessions_page.truncated = true;
    let text = rows(&draw(&mut app, 80, 24)).join("\n");
    for hint in [
        "navigate",
        "open",
        "new",
        "search",
        "all keys & marks",
        "close",
    ] {
        assert!(text.contains(hint), "{hint} is shown:\n{text}");
    }
    assert!(
        text.lines().nth(1).unwrap().contains("Latest 5000 only"),
        "{text}"
    );
}

#[test]
fn the_preview_shows_facts_and_drops_the_conversation_when_too_short() {
    let mut app = page(hostile(), false);
    let tall = rows(&draw(&mut app, 140, 30)).join("\n");
    for wanted in ["Folder", "/w/dashboard", "Model", "Tokens", "95.3k in"] {
        assert!(tall.contains(wanted), "{wanted} is shown:\n{tall}");
    }
    assert!(tall.contains("Loading conversation"), "{tall}");
    let short = rows(&draw(&mut app, 140, 14)).join("\n");
    assert!(short.contains("Folder"), "{short}");
    assert!(
        !short.contains("Loading conversation"),
        "a conversation with no room is not drawn:\n{short}"
    );
}
