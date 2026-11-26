//! `@` completion of rooted paths: query split, listing, filtering and the popup flow.

use std::path::PathBuf;

use vibe_rs::app::App;
use vibe_rs::completion_manager;
use vibe_rs::dir_complete::{self, Entry, Listed, Listing, ReadKey};
use vibe_rs::utils::startup_cache::StartupConfig;

fn entry(name: &str, is_dir: bool) -> Entry {
    Entry {
        name: name.into(),
        is_dir,
    }
}

#[test]
fn only_rooted_queries_split_into_directory_and_prefix() {
    assert_eq!(dir_complete::split("/usr/li"), Some(("/usr/", "li")));
    assert_eq!(dir_complete::split("~/code/da"), Some(("~/code/", "da")));
    assert_eq!(dir_complete::split("../"), Some(("../", "")));
    assert_eq!(dir_complete::split("./src/m"), Some(("./src/", "m")));
    assert_eq!(dir_complete::split("src/main"), None);
    assert_eq!(dir_complete::split("main"), None);
    assert_eq!(dir_complete::split("~"), None);
}

#[test]
fn relative_directories_resolve_under_the_cwd() {
    assert_eq!(
        dir_complete::resolve("../", Some("/repo/app")),
        Some(PathBuf::from("/repo/app/../"))
    );
    assert_eq!(
        dir_complete::resolve("/etc/", Some("/repo")),
        Some(PathBuf::from("/etc/"))
    );
}

#[test]
fn matching_filters_by_prefix_and_marks_directories() {
    let entries = [
        entry("dashboard", true),
        entry("data.txt", false),
        entry("notes", true),
    ];
    assert_eq!(
        dir_complete::matching(&entries, "~/code/", "da", 10),
        vec!["@~/code/dashboard/", "@~/code/data.txt"]
    );
}

#[test]
fn hidden_entries_need_a_dot_prefix() {
    let entries = [entry(".config", true), entry("code", true)];
    assert_eq!(
        dir_complete::matching(&entries, "~/", "", 10),
        vec!["@~/code/"]
    );
    assert_eq!(
        dir_complete::matching(&entries, "~/", ".c", 10),
        vec!["@~/.config/"]
    );
}

#[test]
fn listing_reads_names_and_kinds_and_tells_missing_directories_apart() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::create_dir(dir.path().join("sub")).expect("sub");
    std::fs::write(dir.path().join("file.md"), "").expect("file");
    let mut listed = dir_complete::list(dir.path(), "").expect("readable");
    listed.entries.sort_by(|a, b| a.name.cmp(&b.name));
    assert_eq!(
        listed,
        Listing {
            entries: vec![entry("file.md", false), entry("sub", true)],
            truncated: false,
        }
    );
    assert_eq!(dir_complete::list(&dir.path().join("missing"), ""), None);
}

fn typed(input: &str) -> App {
    let mut app = App::default();
    app.session.cwd = Some("/repo".into());
    app.session.startup_config = StartupConfig {
        enable_file_autocomplete: true,
        ..Default::default()
    };
    app.chat_input.input = input.into();
    app.chat_input.cursor = input.len();
    completion_manager::input_changed(&mut app);
    app
}

fn labels(app: &App) -> Vec<&str> {
    app.completion
        .entries
        .iter()
        .map(|entry| entry.label.as_str())
        .collect()
}

fn land(app: &mut App, dir: &str, entries: Option<Vec<Entry>>) {
    dir_complete::apply_event(
        app,
        Listed {
            key: ReadKey::whole(&PathBuf::from(dir)),
            listing: entries.map(|entries| Listing {
                entries,
                truncated: false,
            }),
        },
    );
}

#[test]
fn a_landed_listing_fills_the_popup_and_enter_on_a_directory_closes_it() {
    let mut app = typed("@/opt/b");
    assert!(
        labels(&app).is_empty(),
        "nothing shows before the read lands"
    );
    land(
        &mut app,
        "/opt/",
        Some(vec![entry("bin", true), entry("boot.log", false)]),
    );
    assert_eq!(labels(&app), vec!["@/opt/bin/", "@/opt/boot.log"]);
    assert!(completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.input, "@/opt/bin/");
    assert!(!completion_manager::is_open(&app));
}

#[test]
fn tab_on_a_directory_lists_it_first_then_its_entries() {
    let mut app = typed("@/opt/b");
    land(&mut app, "/opt/", Some(vec![entry("bin", true)]));
    completion_manager::tab(&mut app);
    assert_eq!(app.chat_input.input, "@/opt/bin/");
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
    land(&mut app, "/opt/bin/", Some(vec![entry("tool", false)]));
    assert_eq!(labels(&app), vec!["@/opt/bin/", "@/opt/bin/tool"]);
    assert_eq!(
        app.completion.selected, 0,
        "the directory itself is preselected"
    );
}

#[test]
fn enter_or_tab_on_the_typed_directory_inserts_it_and_closes() {
    for key in [
        completion_manager::accept as fn(&mut App) -> bool,
        |app: &mut App| {
            completion_manager::tab(app);
            true
        },
    ] {
        let mut app = typed("@/opt/bin/");
        land(&mut app, "/opt/bin/", Some(vec![entry("tool", false)]));
        assert!(key(&mut app));
        assert_eq!(app.chat_input.input, "@/opt/bin/");
        assert!(!completion_manager::is_open(&app));
    }
}

#[test]
fn typing_past_the_directory_drops_its_row() {
    let mut app = typed("@/opt/bin/t");
    land(&mut app, "/opt/bin/", Some(vec![entry("tool", false)]));
    assert_eq!(labels(&app), vec!["@/opt/bin/tool"]);
}

#[test]
fn an_empty_directory_shows_itself_and_a_missing_one_shows_nothing() {
    let mut app = typed("@/opt/empty/");
    land(&mut app, "/opt/empty/", Some(Vec::new()));
    assert_eq!(labels(&app), vec!["@/opt/empty/"]);
    let mut app = typed("@/opt/missing/");
    land(&mut app, "/opt/missing/", None);
    assert!(labels(&app).is_empty());
}

#[test]
fn a_name_with_spaces_is_quoted_and_tab_keeps_completing_inside_the_quotes() {
    let mut app = typed("@/Apps/");
    land(&mut app, "/Apps/", Some(vec![entry("My App.app", true)]));
    completion_manager::navigate(&mut app, true);
    completion_manager::tab(&mut app);
    assert_eq!(app.chat_input.input, "@'/Apps/My App.app/'");
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len() - 1);
    land(
        &mut app,
        "/Apps/My App.app/",
        Some(vec![entry("Info.plist", false)]),
    );
    assert_eq!(
        labels(&app),
        vec!["@/Apps/My App.app/", "@/Apps/My App.app/Info.plist"]
    );
    completion_manager::navigate(&mut app, true);
    assert!(completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.input, "@'/Apps/My App.app/Info.plist' ");
}

#[test]
fn windows_drive_paths_split_only_on_windows() {
    assert_eq!(
        dir_complete::split_on("C:/Users/a", true),
        Some(("C:/Users/", "a"))
    );
    assert_eq!(dir_complete::split_on("C:/Users/a", false), None);
}

#[test]
fn queries_without_a_path_root_keep_using_the_index() {
    let mut app = typed("@opt");
    land(&mut app, "/repo/", Some(vec![entry("opt", true)]));
    assert!(labels(&app).is_empty(), "the empty index matches nothing");
}

#[test]
fn tab_into_a_quoted_folder_holding_an_at_sign_keeps_listing_it() {
    let mut app = typed("@/nm/");
    land(&mut app, "/nm/", Some(vec![entry("@types", true)]));
    completion_manager::navigate(&mut app, true);
    completion_manager::tab(&mut app);
    assert_eq!(app.chat_input.input, "@'/nm/@types/'");
    land(&mut app, "/nm/@types/", Some(vec![entry("node", true)]));
    assert_eq!(labels(&app), vec!["@/nm/@types/", "@/nm/@types/node/"]);
}

#[test]
fn tab_on_the_typed_folder_closes_even_with_backslashes() {
    let mut app = typed("@/opt\\bin\\");
    land(&mut app, "/opt/bin/", Some(vec![entry("tool", false)]));
    assert_eq!(labels(&app)[0], "@/opt/bin/");
    completion_manager::tab(&mut app);
    assert!(!completion_manager::is_open(&app));
}

#[test]
fn an_at_sign_inside_a_closed_quoted_mention_starts_no_completion() {
    let app = typed("@'/nm/@types/'");
    assert!(!completion_manager::active_is_file(&app));
    let app = typed("@'/nm/@types/' @/o");
    assert!(completion_manager::active_is_file(&app));
}

#[test]
fn a_prefix_read_of_a_truncated_directory_adds_entries_past_the_cap() {
    let mut app = typed("@/big/ze");
    dir_complete::apply_event(
        &mut app,
        Listed {
            key: ReadKey::whole(&PathBuf::from("/big/")),
            listing: Some(Listing {
                entries: vec![entry("zed", false)],
                truncated: true,
            }),
        },
    );
    assert_eq!(labels(&app), vec!["@/big/zed"]);
    dir_complete::apply_event(
        &mut app,
        Listed {
            key: ReadKey {
                dir: PathBuf::from("/big/"),
                prefix: "ze".into(),
            },
            listing: Some(Listing {
                entries: vec![entry("zebra.md", false), entry("zed", false)],
                truncated: false,
            }),
        },
    );
    assert_eq!(labels(&app), vec!["@/big/zebra.md", "@/big/zed"]);
}
