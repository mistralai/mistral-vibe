//! The Sessions page list: sections, project groups, started sessions, filters and sort.

use std::collections::HashSet;

use serde_json::json;
use vibe_rs::sessions_page::group::{self, Group};
use vibe_rs::sessions_page::model::{build, Branch, Line, Query, Sort, View};
use vibe_rs::sessions_page::row::{self, Project, Session};

fn session(id: &str, cwd: &str, activity: u64) -> Session {
    Session {
        id: id.into(),
        title: id.into(),
        cwd: Some(cwd.into()),
        repository_root: Some(cwd.into()),
        created_at: activity,
        updated_at: activity,
        bumped_at: Some(activity),
        ..Default::default()
    }
}

fn query<'a>(toggled: &'a HashSet<String>) -> Query<'a> {
    Query {
        sort: Sort::Activity,
        search: "",
        folder: None,
        archived: false,
        gone: false,
        current_id: None,
        current_group: None,
        toggled,
        flat: false,
        group: None,
    }
}

/// Headers as `label count` and rows as their ids, with `├`/`└` for started ones.
fn outline(view: &View, sessions: &[Session]) -> Vec<String> {
    view.lines
        .iter()
        .filter_map(|line| match line {
            Line::Header { label, count } => Some(format!("# {label} {count}")),
            Line::Row {
                session,
                branch,
                folded,
                ..
            } => {
                let prefix = match branch {
                    Some(Branch::Middle) => "├ ",
                    Some(Branch::Last) => "└ ",
                    None => "",
                };
                let fold = if *folded > 0 {
                    format!(" +{folded}")
                } else {
                    String::new()
                };
                Some(format!("{prefix}{}{fold}", sessions[*session].id))
            }
            Line::Blank => None,
        })
        .collect()
}

#[test]
fn projects_rank_by_their_latest_session_and_rows_by_activity() {
    let sessions = vec![
        session("a1", "/w/alpha", 10),
        session("b1", "/w/beta", 30),
        session("a2", "/w/alpha", 20),
    ];
    let toggled = HashSet::new();
    let view = build(&sessions, &[], &query(&toggled));
    assert_eq!(
        outline(&view, &sessions),
        ["# beta 1", "b1", "# alpha 2", "a2", "a1"]
    );
    assert_eq!(view.rows.len(), 3);
}

#[test]
fn the_current_project_comes_first_then_the_latest() {
    let sessions = vec![
        session("a1", "/w/alpha", 10),
        session("b1", "/w/beta", 30),
        session("c1", "/w/gamma", 5),
    ];
    let toggled = HashSet::new();
    let mut query = query(&toggled);
    query.current_group = Some(group::of(&sessions[0], &[]));
    let view = build(&sessions, &[], &query);
    assert_eq!(
        outline(&view, &sessions),
        ["# alpha 1", "a1", "# beta 1", "b1", "# gamma 1", "c1"]
    );
}

#[test]
fn sessions_whose_folder_is_gone_show_only_when_asked() {
    let mut sessions = vec![
        session("here", "/w/alpha", 30),
        session("gone", "/w/alpha", 20),
        session("lost", "/w/beta", 10),
    ];
    sessions[1].cwd_missing = true;
    sessions[2].cwd_missing = true;
    let toggled = HashSet::new();
    let mut q = query(&toggled);
    q.search = "o";
    assert_eq!(
        outline(&build(&sessions, &[], &q), &sessions),
        Vec::<String>::new()
    );
    let q = query(&toggled);
    // A folder whose sessions are all gone leaves no header behind.
    assert_eq!(
        outline(&build(&sessions, &[], &q), &sessions),
        ["# alpha 1", "here"]
    );
    let mut q = query(&toggled);
    q.gone = true;
    assert_eq!(
        outline(&build(&sessions, &[], &q), &sessions),
        ["# alpha 2", "here", "gone", "# beta 1", "lost"]
    );
}

#[test]
fn creation_sort_orders_by_created_at() {
    let mut sessions = vec![session("old", "/w/a", 50), session("new", "/w/a", 10)];
    sessions[0].created_at = 1;
    sessions[1].created_at = 9;
    let toggled = HashSet::new();
    let mut query = query(&toggled);
    query.sort = Sort::Created;
    let view = build(&sessions, &[], &query);
    assert_eq!(outline(&view, &sessions), ["# a 2", "new", "old"]);
}

#[test]
fn started_sessions_nest_under_their_top_starter_folded_by_default() {
    let mut sessions = vec![
        session("root", "/w/a", 10),
        session("child", "/w/a", 30),
        session("grandchild", "/w/a", 20),
    ];
    sessions[1].started_by_session_id = Some("root".into());
    sessions[2].started_by_session_id = Some("child".into());
    let mut toggled = HashSet::new();
    let view = build(&sessions, &[], &query(&toggled));
    assert_eq!(outline(&view, &sessions), ["# a 3", "root +2"]);
    toggled.insert("root".into());
    let view = build(&sessions, &[], &query(&toggled));
    assert_eq!(
        outline(&view, &sessions),
        ["# a 3", "root", "├ child", "└ grandchild"]
    );
}

#[test]
fn a_family_holding_the_current_session_is_open() {
    let mut sessions = vec![session("root", "/w/a", 10), session("child", "/w/a", 30)];
    sessions[1].started_by_session_id = Some("root".into());
    let toggled = HashSet::new();
    let mut query = query(&toggled);
    query.current_id = Some("child");
    let view = build(&sessions, &[], &query);
    assert_eq!(outline(&view, &sessions), ["# a 2", "root", "└ child"]);
}

#[test]
fn a_session_whose_starter_is_not_listed_stands_alone() {
    let mut sessions = vec![session("orphan", "/w/a", 10)];
    sessions[0].started_by_session_id = Some("gone".into());
    let toggled = HashSet::new();
    let view = build(&sessions, &[], &query(&toggled));
    assert_eq!(outline(&view, &sessions), ["# a 1", "orphan"]);
}

#[test]
fn search_folder_filter_and_archived_view() {
    let mut sessions = vec![
        session("fix-login", "/w/a", 10),
        session("docs", "/w/b", 20),
        session("old", "/w/a", 5),
    ];
    sessions[2].archived_at = Some(1);
    let toggled = HashSet::new();
    let mut q = query(&toggled);
    q.search = "LOGIN";
    assert_eq!(
        outline(&build(&sessions, &[], &q), &sessions),
        ["# a 1", "fix-login"]
    );
    let mut q = query(&toggled);
    q.folder = Some("/w/b");
    assert_eq!(
        outline(&build(&sessions, &[], &q), &sessions),
        ["# b 1", "docs"]
    );
    let mut q = query(&toggled);
    q.archived = true;
    assert_eq!(
        outline(&build(&sessions, &[], &q), &sessions),
        ["# a 1", "old"]
    );
}

#[test]
fn a_project_stamp_wins_over_the_repository() {
    let projects = vec![Project {
        id: "p1".into(),
        name: "Website".into(),
        root_paths: vec!["/w/site".into()],
        cloud: None,
    }];
    let mut stamped = session("s", "/elsewhere", 1);
    stamped.project = Some(row::ProjectStamp {
        kind: "local".into(),
        id: Some("p1".into()),
    });
    assert_eq!(group::of(&stamped, &projects).label, "Website");
    let in_root = session("r", "/w/site", 1);
    assert_eq!(group::of(&in_root, &projects).label, "Website");
    let plain = session("x", "/w/tool", 1);
    let expected = Group {
        key: "/w/tool".into(),
        label: "tool".into(),
    };
    assert_eq!(group::of(&plain, &projects), expected);
}

#[test]
fn rows_without_the_new_fields_and_malformed_rows_still_list() {
    let page = json!({"items": [
        {"id": "old-server", "title": null, "preview": "hello", "updatedAt": 5},
        {"preview": "no id"},
        {"id": "subagent", "parentSessionId": "old-server"},
        {"id": "full", "status": {"type": "failed"}, "repositoryRoot": "/w/r", "isUnseen": true},
    ]});
    let sessions = row::sessions(&page);
    let ids: Vec<_> = sessions.iter().map(|s| s.id.as_str()).collect();
    assert_eq!(ids, ["old-server", "full"]);
    assert_eq!(sessions[0].label(), "hello");
    assert_eq!(sessions[0].activity(), 5);
    assert_eq!(sessions[1].status, "failed");
    assert_eq!(sessions[1].repository_root.as_deref(), Some("/w/r"));
}

#[test]
fn folder_groups_sharing_a_name_show_just_enough_of_their_path() {
    let sessions = vec![
        session("a", "/w/vibe-4359/vibe", 30),
        session("b", "/w/vibe-5442/vibe", 20),
        session("c", "/w/dashboard", 10),
    ];
    let toggled = HashSet::new();
    let view = build(&sessions, &[], &query(&toggled));
    assert_eq!(
        outline(&view, &sessions),
        [
            "# /…/vibe-4359/vibe 1",
            "a",
            "# /…/vibe-5442/vibe 1",
            "b",
            "# dashboard 1",
            "c"
        ]
    );
}

#[test]
fn a_session_in_a_linked_worktree_reads_as_its_worktree_and_subfolder() {
    let mut worktree = session("a", "/w/repo-feature/vibe", 1);
    worktree.repository_root = Some("/w/repo".into());
    worktree.checkout_root = Some("/w/repo-feature".into());
    assert_eq!(worktree.location().as_deref(), Some("repo-feature › vibe"));

    let mut main = session("b", "/w/repo/vibe", 1);
    main.repository_root = Some("/w/repo".into());
    main.checkout_root = Some("/w/repo".into());
    assert_eq!(main.location().as_deref(), Some("vibe"));

    let mut top = session("c", "/w/repo", 1);
    top.checkout_root = Some("/w/repo".into());
    assert_eq!(top.location(), None);
}

#[test]
fn a_session_without_a_known_checkout_has_no_location() {
    let mut legacy = session("a", "/w/old/vibe", 1);
    legacy.repository_root = None;
    assert_eq!(legacy.location(), None);
}

#[test]
fn a_row_reads_cwd_missing_from_the_listing() {
    let rows = row::sessions(&json!({"items": [
        {"id": "a", "cwd": "/w/gone", "cwdMissing": true, "checkoutRoot": "/w/gone"},
        {"id": "b", "cwd": "/w/here"}
    ]}));
    assert!(rows[0].cwd_missing);
    assert_eq!(rows[0].checkout_root.as_deref(), Some("/w/gone"));
    assert!(!rows[1].cwd_missing);
}
