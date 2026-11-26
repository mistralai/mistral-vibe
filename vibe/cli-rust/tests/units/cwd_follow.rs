//! `cwd::follow`: the client state that moves with the session's folder.

use vibe_rs::app::App;
use vibe_rs::worktree::WorktreeInfo;

fn worktree() -> WorktreeInfo {
    WorktreeInfo {
        name: "feature".into(),
        branch: "feature".into(),
        path: "/work/worktrees/feature".into(),
        created: true,
    }
}

fn app_in(cwd: &str) -> App {
    let mut app = App::default();
    app.session.cwd = Some(cwd.into());
    app.session.agent_config.cwd = Some(cwd.into());
    app.session.worktree = Some(worktree());
    app
}

#[test]
fn follow_moves_the_cwd_and_remembers_the_previous_one() {
    let mut app = app_in("/work/repo");
    vibe_rs::cwd::follow(&mut app, "/work/other".into());
    assert_eq!(app.session.cwd.as_deref(), Some("/work/other"));
    assert_eq!(app.session.agent_config.cwd.as_deref(), Some("/work/other"));
    assert_eq!(app.session.previous_cwd.as_deref(), Some("/work/repo"));
}

#[test]
fn leaving_the_worktree_forgets_it() {
    let mut app = app_in("/work/worktrees/feature");
    vibe_rs::cwd::follow(&mut app, "/work/repo".into());
    assert_eq!(app.session.worktree, None);
}

#[test]
fn moving_inside_the_worktree_keeps_it() {
    let mut app = app_in("/work/worktrees/feature");
    vibe_rs::cwd::follow(&mut app, "/work/worktrees/feature/src".into());
    assert_eq!(app.session.worktree, Some(worktree()));
}

#[test]
fn following_the_same_folder_keeps_the_previous_one() {
    let mut app = app_in("/work/repo");
    app.session.previous_cwd = Some("/old".into());
    vibe_rs::cwd::follow(&mut app, "/work/repo".into());
    assert_eq!(app.session.previous_cwd.as_deref(), Some("/old"));
}

#[test]
fn the_index_parks_at_the_new_root_and_a_broad_root_stays_held() {
    let mut app = app_in("/work/repo");
    vibe_rs::cwd::follow(&mut app, "/work/other".into());
    let files = &app.completion.files;
    assert!(!files.running() && !files.held());
    assert_eq!(files.parked_root.as_deref(), Some("/work/other".as_ref()));
    vibe_rs::cwd::follow(&mut app, "/".into());
    assert!(app.completion.files.held(), "/ is never indexed");
}

#[test]
fn a_move_drops_the_launch_folders_trust_but_keeps_its_extra_roots() {
    let mut app = app_in("/work/repo");
    app.session.agent_config.trust_workspace = true;
    app.session.agent_config.workspace_roots = vec!["/work/shared".into()];
    vibe_rs::cwd::follow(&mut app, "/elsewhere".into());
    assert!(!app.session.agent_config.trust_workspace);
    assert_eq!(app.session.agent_config.workspace_roots, ["/work/shared"]);
}

#[test]
fn following_the_same_folder_keeps_its_trust() {
    let mut app = app_in("/work/repo");
    app.session.agent_config.trust_workspace = true;
    vibe_rs::cwd::follow(&mut app, "/work/repo".into());
    assert!(app.session.agent_config.trust_workspace);
}

#[test]
fn cd_back_to_the_checkout_after_entering_a_worktree_moves() {
    let mut app = app_in("/work/repo");
    app.session.worktree = None;
    // `enter_worktree` moves the session into the worktree.
    vibe_rs::worktree::track(&mut app, worktree());
    assert_eq!(
        app.session.agent_config.cwd.as_deref(),
        Some("/work/worktrees/feature")
    );
    vibe_rs::cwd::follow(&mut app, "/work/repo".into());
    assert_eq!(app.session.worktree, None, "the worktree is left");
    assert_eq!(
        app.session.previous_cwd.as_deref(),
        Some("/work/worktrees/feature")
    );
}
