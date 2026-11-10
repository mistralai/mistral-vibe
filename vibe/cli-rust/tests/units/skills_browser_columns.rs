//! `/skills` list columns: their cells, and which a narrow pane drops.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::skills_browser::labels::cells;
use vibe_rs::ui::skills_browser::columns::Columns;

use crate::skills_browser_support::{catalog, opened, pin, pins, press, registry, shared};
use crate::support::{render, rows, stub};

#[test]
fn cells_read_scope_source_and_the_pinned_version() {
    let skills = pins(vec![
        pin("review", "local", "global"),
        registry("deploy", "project", 3, Some("latest")),
        registry("deploy", "global", 2, None),
        pin("pdf", "plugin", "global"),
        pin("shared", "registry", "session"),
    ]);
    let cells: Vec<[String; 3]> = skills.iter().map(cells).collect();
    let expected = [
        ["Global", "Local", ""],
        ["Project", "Shared", "v3 (latest)"],
        ["Global", "Shared", "v2"],
        ["", "Plugin", ""],
        ["Session", "Shared", ""],
    ];
    assert_eq!(cells, expected.map(|row| row.map(str::to_owned)));
}

#[test]
fn narrower_panes_drop_version_then_source_then_scope_and_keep_the_badge_room() {
    let long = pin(
        "summarize-pull-request-review-discussion",
        "local",
        "project",
    );
    let app = opened(
        vec![long, registry("deploy", "global", 2, Some("stable"))],
        None,
    );
    let state = &app.skills_browser;
    let fits = [
        (114, [7, 6, 11]),
        (73, [7, 6, 0]),
        (60, [7, 0, 0]),
        (52, [0, 0, 0]),
    ];
    for (pane, widths) in fits {
        let columns = Columns::fit(state, state.rows(), pane);
        assert_eq!(
            (columns.name, columns.installed),
            (40, widths),
            "{pane} columns"
        );
    }
    let mut newer = shared();
    newer.updates.insert("deploy".to_owned(), 9);
    let frozen = opened(vec![registry("deploy", "global", 2, None)], Some(newer));
    let state = &frozen.skills_browser;
    assert_eq!(Columns::fit(state, state.rows(), 45).installed, [6, 6, 7]);
    let without_version = Columns::fit(state, state.rows(), 44).installed;
    assert_eq!(
        without_version,
        [6, 6, 0],
        "the update badge keeps its room"
    );
}

#[test]
fn narrower_panes_drop_the_shared_version_then_sharing() {
    let long = json!({
        "name": "summarize-pull-request-review-discussion",
        "skillId": "id-long",
        "description": "Long",
        "latestVersion": 12,
        "sharingScope": "workspace",
    });
    let client = stub();
    let mut app = opened(
        Vec::new(),
        Some(catalog(json!({"skills": [long], "loaded": true}))),
    );
    press(&mut app, &client, KeyCode::Tab);
    let state = &app.skills_browser;
    let fits = [(65, (9, 7)), (64, (9, 0)), (56, (9, 0)), (55, (0, 0))];
    for (pane, widths) in fits {
        let columns = Columns::fit(state, state.rows(), pane);
        assert_eq!((columns.sharing, columns.version), widths, "{pane} columns");
    }
    let screen = rows(&render(66, 40, |frame| app.draw(frame)));
    let heading = screen.iter().find(|row| row.contains("Sharing"));
    let heading = heading.expect("the Shared heading keeps Sharing");
    assert!(!heading.contains("Vers"), "{heading}");
    assert!(screen.iter().all(|row| !row.contains("v12")));
}

#[test]
fn a_box_too_narrow_for_its_scrollbar_still_draws() {
    let many = (0..30)
        .map(|n| pin(&format!("skill-{n}"), "local", "global"))
        .collect();
    let mut app = opened(many, None);
    for width in 1..=6 {
        render(width, 40, |frame| app.draw(frame));
    }
}
