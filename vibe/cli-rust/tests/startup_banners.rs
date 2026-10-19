//! What's-new and VS Code promo mounts and the cache stamps they leave; separate binary for env isolation.

use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard};

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::server::Client;
use vibe_rs::startup::banners;
use vibe_rs::utils::whats_new_cache;

/// Guards VIBE_HOME, VIBE_WHATS_NEW_FILE and TERM_PROGRAM, which are process-global.
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
    /// A launch whose cache holds `cache_toml` and whose what's-new file holds `body`.
    fn new(cache_toml: &str, body: Option<&str>, vscode: bool) -> Self {
        let guard = ENV_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let home = tempfile::tempdir().unwrap();
        std::env::set_var("VIBE_HOME", home.path());
        std::fs::write(home.path().join("cache.toml"), cache_toml).unwrap();
        let whats_new = home.path().join("whats_new.md");
        if let Some(body) = body {
            std::fs::write(&whats_new, body).unwrap();
        }
        std::env::set_var("VIBE_WHATS_NEW_FILE", whats_new);
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
        let mut app = App::default();
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
fn an_unseen_version_shows_its_whats_new_and_marks_it_seen() {
    let launch = Launch::new(UNSEEN, Some("# What's new\n\n- Faster startup\n"), false);
    let app = launch.mount();

    assert_eq!(
        whats_new(&app).as_deref(),
        Some("# What's new\n\n- Faster startup")
    );
    assert!(app.view.promo.is_none());
    assert!(marked_seen(&launch));
}

#[test]
fn an_empty_whats_new_shows_nothing_but_still_marks_the_version_seen() {
    let launch = Launch::new(UNSEEN, Some("  \n\t\n"), false);
    let app = launch.mount();

    assert_eq!(whats_new(&app), None);
    assert!(marked_seen(&launch));
}

#[test]
fn a_missing_whats_new_shows_nothing_but_still_marks_the_version_seen() {
    let launch = Launch::new(UNSEEN, None, false);
    let app = launch.mount();

    assert_eq!(whats_new(&app), None);
    assert!(marked_seen(&launch));
}

#[test]
fn the_promo_shows_alone_without_whats_new_and_counts_its_display() {
    let launch = Launch::new("", None, true);
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
    let launch = Launch::new("[vscode_extension_promo]\nshown_count = 10\n", None, true);
    let app = launch.mount();

    assert!(app.view.promo.is_none());
    assert!(launch.cache_toml().contains("shown_count = 10"));
}

#[test]
fn the_promo_joins_the_whats_new_message_when_both_show() {
    let launch = Launch::new(UNSEEN, Some("# What's new\n"), true);
    let app = launch.mount();

    let body = whats_new(&app).expect("what's new shows");
    assert!(body.starts_with("# What's new\n\n_Btw, we also have a new "));
    assert!(body.contains(PROMO_LINK));
    assert!(app.view.promo.is_none(), "no separate promo message");
    assert!(launch.cache_toml().contains("shown_count = 1"));
}
