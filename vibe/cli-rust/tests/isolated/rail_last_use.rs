//! The rail's last use survives into the next session through `cache.toml`.

use vibe_rs::app::App;

#[test]
fn a_saved_use_is_what_the_next_session_loads() {
    let home = tempfile::tempdir().unwrap();
    std::env::set_var("VIBE_HOME", home.path());

    let mut app = App::default();
    vibe_rs::rail::load_use(&mut app);
    assert_eq!(app.rail.used_at, None);
    app.rail.used_at = Some(1_700_000_000);
    vibe_rs::rail::save_use(&mut app);

    let mut next = App::default();
    vibe_rs::rail::load_use(&mut next);
    assert_eq!(next.rail.used_at, Some(1_700_000_000));
}
