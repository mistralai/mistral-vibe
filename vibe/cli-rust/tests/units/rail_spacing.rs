//! Rail chips sit exactly one ` · ` apart and end on visible text, whatever each chip shows; a dimmed Ctrl+X hint leads them.

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::todo_tracker::{TodoItem, TodoStatus};

use crate::artifacts_actions::{app_with, pr};
use crate::support::{rail, row};

fn app(goal: &str, prs: u64, child: SessionStatus, letters: bool) -> App {
    let mut app = app_with((1..=prs).map(|n| pr(n, 10, json!({}))).collect());
    app.goal.goal = Some(
        serde_json::from_value(json!({
            "goalId": "goal-1", "revision": 1, "objectiveRevision": 1,
            "objective": "Ship it", "status": goal, "tokenBudget": 50000,
            "tokensUsed": 12500, "timeUsedSeconds": 240,
        }))
        .unwrap(),
    );
    app.session.background_tasks = vec![serde_json::from_value(json!({
        "processId": "p", "command": "watch", "status": "running",
    }))
    .unwrap()];
    app.todo_tracker.record(vec![TodoItem {
        id: "1".into(),
        content: "Ship".into(),
        status: TodoStatus::Completed,
    }]);
    app.subagents.replace_child_session(PublicChildSession {
        id: "a".into(),
        name: "explore-1".into(),
        agent_type: "explore".into(),
        status: child,
        ..Default::default()
    });
    vibe_rs::subagents::refresh(&mut app);
    if letters {
        vibe_rs::rail::enter(&mut app);
    }
    app
}

#[test]
fn every_chip_is_one_dot_from_the_next() {
    let goals = [
        "active",
        "paused",
        "complete",
        "blocked",
        "budget_limited",
        "unknown",
    ];
    let children = [SessionStatus::Running, SessionStatus::Idle];
    for goal in goals {
        for prs in [1, 2] {
            for child in children {
                for letters in [false, true] {
                    let mut app = app(goal, prs, child, letters);
                    let line = row(&rail(&mut app, 200), 0);
                    let cells: Vec<&str> = line.split("").filter(|c| !c.is_empty()).collect();
                    let hits = app.rail.hits.clone();
                    assert_eq!(hits.len(), 5, "{line}");
                    for pair in hits.windows(2) {
                        let ((_, x, width), (_, next, _)) = (pair[0], pair[1]);
                        let between: String =
                            cells[usize::from(x + width)..usize::from(next)].concat();
                        assert_eq!(between, " · ", "{goal} {prs} {child:?}: {line:?}");
                    }
                    for (_, x, width) in hits {
                        assert_ne!(cells[usize::from(x + width - 1)], " ", "{line:?}");
                    }
                }
            }
        }
    }
}

#[test]
fn a_complete_goal_is_just_its_check_and_name() {
    let mut app = app("complete", 1, SessionStatus::Running, false);
    let row = row(&rail(&mut app, 200), 0);
    assert!(row.contains("✓ goal · "), "{row:?}");
    assert!(!row.contains("tokens"), "{row:?}");
}

#[test]
fn a_dimmed_ctrl_x_hint_leads_the_rail_until_focus_or_a_narrow_row() {
    let mut app = crate::rail::with_items(false);
    let buffer = rail(&mut app, 80);
    let line = row(&buffer, 0);
    assert!(line.contains("Ctrl+X to navigate · ▶ goal"), "{line:?}");
    let start = line.find('C').expect("hint") as u16;
    assert!(buffer[(start, 0)]
        .modifier
        .contains(ratatui::style::Modifier::DIM));

    let narrow = row(&rail(&mut app, 24), 0);
    assert!(
        !narrow.contains("Ctrl+X"),
        "the hint goes before any chip shrinks: {narrow:?}"
    );
    assert!(narrow.contains("▶ goal 4m · ≡ 0/1"), "{narrow:?}");

    vibe_rs::rail::enter(&mut app);
    assert!(
        !row(&rail(&mut app, 80), 0).contains("Ctrl+X"),
        "focus hides it"
    );
}
