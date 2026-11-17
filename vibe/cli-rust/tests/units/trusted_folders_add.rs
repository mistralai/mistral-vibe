//! `/trusted-folders` add view: Enter trusts the highlighted folder, and the view warns about what it covers.

use vibe_rs::app::App;
use vibe_rs::server::{TrustFolderCandidate, TrustFolderInspection, WorkspaceTrustFolders};
use vibe_rs::trusted_folders::add::{apply_inspection, Add};
use vibe_rs::trusted_folders::add_text::{details, help, row, status, Tone};
use vibe_rs::trusted_folders::{apply_event, close, Event, Target};

fn candidate(path: &str) -> TrustFolderCandidate {
    TrustFolderCandidate {
        path: path.to_owned(),
        value: format!("{path}/"),
        ..TrustFolderCandidate::default()
    }
}

fn add(typed_problem: Option<&str>, candidates: Vec<TrustFolderCandidate>) -> Add {
    Add {
        inspection: Some(TrustFolderInspection {
            typed_problem: typed_problem.map(str::to_owned),
            candidates,
        }),
        ..Add::default()
    }
}

fn keys(add: &Add) -> Vec<&'static str> {
    help(add).iter().map(|(key, _)| *key).collect()
}

#[test]
fn enter_trusts_the_highlighted_folder_and_warns_about_its_inside() {
    let mut add = add(
        None,
        vec![candidate("/srv/project"), candidate("/srv/project/api")],
    );
    add.candidates.select(1);

    let details = details(&add);

    assert!(add.addable());
    assert_eq!(
        add.highlighted().map(|c| c.path.as_str()),
        Some("/srv/project/api")
    );
    assert_eq!(details[0].0, Tone::Warning);
    assert!(details[0]
        .1
        .starts_with("Trusting /srv/project/api trusts every folder inside it too"));
    assert_eq!(keys(&add), ["↑↓", "Enter", "Tab", "Esc"]);
}

#[test]
fn untrusted_folders_inside_and_a_trusted_parent_are_named() {
    let add = add(
        None,
        vec![TrustFolderCandidate {
            untrusted_inside: vec!["/srv/project/a".to_owned(), "/srv/project/b".to_owned()],
            untrusted_inside_count: 3,
            trusted_by: Some("/srv".to_owned()),
            ..candidate("/srv/project")
        }],
    );

    let lines: Vec<String> = details(&add).into_iter().map(|(_, line)| line).collect();

    assert!(lines
        .iter()
        .any(|line| line.ends_with("keeps it trusted if that entry is removed.")));
    assert!(
        lines.contains(&"Stays untrusted: /srv/project/a, /srv/project/b and 1 more".to_owned())
    );
}

#[test]
fn a_path_naming_no_folder_says_its_closest_parent_is_offered() {
    let add = add(Some("missing"), vec![candidate("/srv")]);

    assert_eq!(status(&add).map(|(tone, _)| tone), Some(Tone::Error));
    assert!(add.addable(), "the closest existing parent can be trusted");
}

#[test]
fn home_and_already_trusted_folders_are_listed_but_not_addable() {
    let home = TrustFolderCandidate {
        problem: Some("home".to_owned()),
        ..candidate("/home/user")
    };
    let trusted = TrustFolderCandidate {
        stored: Some("trusted".to_owned()),
        ..candidate("/srv/repo")
    };

    for (candidate, note) in [(home, "home folder"), (trusted, "already trusted")] {
        let add = add(None, vec![candidate.clone()]);

        assert!(!add.addable());
        assert_eq!(row(&candidate).1, Some(note));
        assert!(!keys(&add).contains(&"Enter"));
    }
}

fn open_on(generation: u64, candidates: Vec<TrustFolderCandidate>) -> App {
    let mut app = App::default();
    app.trusted_folders.add = Some(Add {
        generation,
        ..Add::default()
    });
    let answer = TrustFolderInspection {
        typed_problem: None,
        candidates,
    };
    apply_inspection(&mut app, generation, Ok(answer));
    app
}

fn view(app: &App) -> &Add {
    app.trusted_folders
        .add
        .as_ref()
        .expect("the add view is open")
}

#[test]
fn an_answer_to_an_older_request_is_dropped() {
    let mut app = open_on(2, vec![candidate("/srv/now")]);

    let stale = TrustFolderInspection {
        typed_problem: None,
        candidates: vec![candidate("/srv/before")],
    };
    apply_inspection(&mut app, 1, Ok(stale));

    assert_eq!(
        view(&app).highlighted().map(|c| c.path.as_str()),
        Some("/srv/now")
    );
    assert!(view(&app).addable());
}

#[test]
fn enter_waits_for_the_answer_to_the_latest_request() {
    let mut app = open_on(1, vec![candidate("/srv/before")]);
    if let Some(add) = app.trusted_folders.add.as_mut() {
        add.generation = 2;
    }

    assert!(
        !view(&app).addable(),
        "the field changed since these candidates"
    );
}

#[test]
fn a_failed_answer_clears_the_candidates_and_says_why() {
    let mut app = open_on(1, vec![candidate("/srv/before")]);

    apply_inspection(&mut app, 1, Err("Not a usable path: ~nobody".to_owned()));

    assert!(view(&app).candidates().is_empty());
    assert!(!view(&app).addable());
    assert_eq!(
        status(view(&app)),
        Some((Tone::Error, "Not a usable path: ~nobody".to_owned()))
    );
}

#[test]
fn page_down_moves_by_a_page_and_stops_at_the_last_folder() {
    let folders = (0..8)
        .map(|index| candidate(&format!("/srv/{index}")))
        .collect();
    let mut app = open_on(1, folders);
    let add = app.trusted_folders.add.as_mut().expect("open");
    let count = add.candidates().len();

    add.candidates.page(true, count, 5);
    let after_one = add.candidates.selected();
    add.candidates.page(true, count, 5);

    assert_eq!((after_one, add.candidates.selected()), (5, 7));
}

fn added(app: &mut App) {
    apply_event(
        app,
        Event::Updated {
            path: "/srv/project".to_owned(),
            target: Target::Added,
            answer: Ok(WorkspaceTrustFolders::default()),
        },
    );
}

#[test]
fn a_saved_folder_closes_the_add_view_that_saved_it() {
    let mut app = open_on(1, vec![candidate("/srv/project")]);
    app.trusted_folders.updating = true;
    app.trusted_folders.add.as_mut().expect("open").saving = true;

    added(&mut app);

    assert!(app.trusted_folders.add.is_none());
    assert!(!app.trusted_folders.updating);
}

#[test]
fn a_late_save_answer_leaves_a_reopened_add_view_open() {
    // Esc closed the view that saved; this one was opened while the save was in flight.
    let mut app = open_on(1, vec![candidate("/srv/other")]);
    app.trusted_folders.updating = true;

    added(&mut app);

    assert!(app.trusted_folders.add.is_some());
    assert!(!app.trusted_folders.updating);
}

#[test]
fn closing_and_reopening_keeps_an_update_in_flight_guarded() {
    let mut app = App::default();
    app.trusted_folders.open = true;
    app.trusted_folders.updating = true;

    close(&mut app);
    apply_event(&mut app, Event::Read(Ok(WorkspaceTrustFolders::default())));

    assert!(app.trusted_folders.open);
    assert!(
        app.trusted_folders.updating,
        "the earlier request has not answered yet"
    );
}
