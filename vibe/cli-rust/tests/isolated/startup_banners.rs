//! What's-new and VS Code promo mounts and the cache stamps they leave; separate binary for env isolation.

use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard};

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::server::Client;
use vibe_rs::startup::banners;
use vibe_rs::utils::whats_new_cache;

/// Guards VIBE_HOME, VIBE_WHATS_NEW_DIR and TERM_PROGRAM, which are process-global.
static ENV_LOCK: Mutex<()> = Mutex::new(());

const VERSION: &str = env!("CARGO_PKG_VERSION");
const PROMO_LINK: &str = "[VS Code extension](vscode:extension/mistralai.mistral-vibe-code)";
const UNSEEN: &str =
    "[update_cache]\nlatest_version = \"0.0.1\"\nstored_at_timestamp = 1\nseen_whats_new_version = \"0.0.0\"\n";

struct Launch {
    home: tempfile::TempDir,
    _guard: MutexGuard<'static, ()>,
}

impl Launch {
    /// A launch whose cache holds `cache_toml` and whose history holds `notes`, one `(version, body)` per file.
    fn new(cache_toml: &str, notes: &[(&str, &str)], vscode: bool) -> Self {
        let guard = ENV_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let home = tempfile::tempdir().unwrap();
        std::env::set_var("VIBE_HOME", home.path());
        std::fs::write(home.path().join("cache.toml"), cache_toml).unwrap();
        let history = home.path().join("whats_new");
        std::fs::create_dir(&history).unwrap();
        for (version, body) in notes {
            std::fs::write(history.join(format!("{version}.md")), body).unwrap();
        }
        std::env::set_var("VIBE_WHATS_NEW_DIR", history);
        std::env::remove_var("TERM_PROGRAM_VERSION");
        if vscode {
            std::env::set_var("TERM_PROGRAM", "vscode");
        } else {
            std::env::set_var("TERM_PROGRAM", "Apple_Terminal");
        }
        Self {
            home,
            _guard: guard,
        }
    }

    fn mount(&self) -> App {
        self.mount_on(App::default())
    }

    fn mount_on(&self, mut app: App) -> App {
        banners::mount(&mut app, &json!({"runtime": {}}), &Arc::new(Client::stub()));
        app
    }

    fn cache_toml(&self) -> String {
        std::fs::read_to_string(self.cache_path()).unwrap_or_default()
    }

    fn cache_path(&self) -> PathBuf {
        self.home.path().join("cache.toml")
    }
}

fn whats_new(app: &App) -> Option<String> {
    let id = &app.session.whats_new.as_ref()?.id;
    let content = app.view.transcript.entry_content(id)?;
    content[0]["text"].as_str().map(str::to_owned)
}

fn marked_seen(launch: &Launch) -> bool {
    launch
        .cache_toml()
        .contains(&format!("seen_whats_new_version = \"{VERSION}\""))
        && !whats_new_cache::should_show(VERSION)
}

#[test]
fn every_release_since_the_last_visit_shows_newest_first_and_marks_the_version_seen() {
    let notes = [
        ("0.0.1", "- Too old"),
        ("0.1.0", "- Faster startup\n"),
        ("0.2.0", "- Branches"),
    ];
    let cache = UNSEEN.replace("\"0.0.0\"", "\"0.0.1\"");
    let launch = Launch::new(&cache, &notes, false);
    let app = launch.mount();

    assert_eq!(
        whats_new(&app).as_deref(),
        Some(
            "**v0.2.0**\n\n- Branches\n\n\
             **v0.1.0**\n\n- Faster startup\n\n\
             _Browse every release with `/release-notes`._"
        )
    );
    assert!(app.view.promo.is_none());
    assert!(marked_seen(&launch));
}

#[test]
fn a_cache_without_a_seen_version_shows_only_the_newest_release() {
    let cache = "[update_cache]\nlatest_version = \"0.0.1\"\nstored_at_timestamp = 1\n";
    let launch = Launch::new(cache, &[("0.1.0", "- Older"), ("0.2.0", "- Newer")], false);
    let app = launch.mount();

    let body = whats_new(&app).expect("what's new shows");
    assert!(body.starts_with("**v0.2.0**\n\n- Newer\n\n_Browse"));
    assert!(marked_seen(&launch));
}

#[test]
fn no_new_release_notes_show_nothing_but_still_mark_the_version_seen() {
    let launch = Launch::new(
        UNSEEN,
        &[("0.0.0", "- Already seen"), ("0.1.0", "  \n\t\n")],
        false,
    );
    let app = launch.mount();

    assert_eq!(whats_new(&app), None);
    assert!(marked_seen(&launch));
}

#[test]
fn a_resumed_session_waits_for_a_fresh_one_before_showing_what_s_new() {
    let launch = Launch::new(UNSEEN, &[("0.1.0", "- Faster startup")], false);
    let mut resumed = App::default();
    resumed.view.transcript.add(&json!({
        "entry": {
            "id": "saved",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Saved reply"}],
            "generationStatus": "completed",
        }
    }));
    let app = launch.mount_on(resumed);

    assert_eq!(whats_new(&app), None);
    assert!(!launch
        .cache_toml()
        .contains(&format!("seen_whats_new_version = \"{VERSION}\"")));

    let app = launch.mount();
    assert!(whats_new(&app).is_some_and(|body| body.contains("- Faster startup")));
    assert!(marked_seen(&launch));
}

#[test]
fn the_startup_resume_picker_defers_what_s_new_to_a_fresh_session() {
    let launch = Launch::new(UNSEEN, &[("0.1.0", "- Faster startup")], false);
    let mut picking = App::default();
    picking.session.resume_picker_shown = true;
    let app = launch.mount_on(picking);

    assert_eq!(whats_new(&app), None);
    assert!(!launch
        .cache_toml()
        .contains(&format!("seen_whats_new_version = \"{VERSION}\"")));
}

#[test]
fn a_fresh_install_shows_nothing_and_stamps_nothing() {
    let launch = Launch::new("", &[("0.1.0", "- Faster startup")], false);
    let app = launch.mount();

    assert_eq!(whats_new(&app), None);
    assert!(!launch.cache_toml().contains("seen_whats_new_version"));
}

#[test]
fn the_promo_shows_alone_without_whats_new_and_counts_its_display() {
    let launch = Launch::new("", &[], true);
    let app = launch.mount();

    assert_eq!(whats_new(&app), None);
    assert!(app
        .view
        .promo
        .as_deref()
        .is_some_and(|promo| promo.contains(PROMO_LINK)));
    assert!(launch
        .cache_toml()
        .contains("[vscode_extension_promo]\nshown_count = 1"));
}

#[test]
fn the_promo_stops_after_ten_displays() {
    let launch = Launch::new("[vscode_extension_promo]\nshown_count = 10\n", &[], true);
    let app = launch.mount();

    assert!(app.view.promo.is_none());
    assert!(launch.cache_toml().contains("shown_count = 10"));
}

#[test]
fn the_promo_joins_the_whats_new_message_when_both_show() {
    let launch = Launch::new(UNSEEN, &[("0.1.0", "- New")], true);
    let app = launch.mount();

    let body = whats_new(&app).expect("what's new shows");
    assert!(body.contains("`/release-notes`._\n\n_Btw, we also have a new "));
    assert!(body.contains(PROMO_LINK));
    assert!(app.view.promo.is_none(), "no separate promo message");
    assert!(launch.cache_toml().contains("shown_count = 1"));
}
