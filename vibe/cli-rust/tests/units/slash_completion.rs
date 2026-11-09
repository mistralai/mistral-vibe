//! Slash-command popup matching: fuzzy, case-insensitive, prefix-first, live skill entries and origins.

use vibe_rs::app::App;
use vibe_rs::completion_manager as completion;
use vibe_rs::input_modes::{classify, ClassifiedInput, InputMode};
use vibe_rs::skill::{Skill, SkillOrigin};

fn type_slash(app: &mut App, query: &str) -> Vec<String> {
    app.chat_input.mode = InputMode::Slash;
    app.chat_input.input = query.into();
    app.chat_input.cursor = query.len();
    completion::input_changed(app);
    app.completion
        .entries
        .iter()
        .map(|entry| entry.label.clone())
        .collect()
}

#[test]
fn a_substring_or_a_later_hyphen_segment_matches() {
    let mut app = App::default();
    assert_eq!(
        type_slash(&mut app, "retention")
            .first()
            .map(String::as_str),
        Some("/data-retention")
    );
    assert_eq!(
        type_slash(&mut app, "level").first().map(String::as_str),
        Some("/log-level")
    );
}

#[test]
fn scattered_characters_match_and_rank_below_prefixes() {
    let mut app = App::default();
    assert!(type_slash(&mut app, "dtrt").contains(&"/data-retention".to_owned()));

    let labels = type_slash(&mut app, "re");
    let first_other = labels
        .iter()
        .position(|label| !label.starts_with("/re"))
        .unwrap_or(labels.len());
    assert!(first_other > 0, "{labels:?}");
    assert!(
        labels[first_other..]
            .iter()
            .all(|label| !label.starts_with("/re")),
        "every prefix match ranks first: {labels:?}"
    );
}

#[test]
fn matching_ignores_case() {
    let mut app = App::default();
    assert_eq!(
        type_slash(&mut app, "HELP").first().map(String::as_str),
        Some("/help")
    );
}

#[test]
fn an_alias_is_suggested_under_its_own_name() {
    let mut app = App::default();
    assert_eq!(
        type_slash(&mut app, "new").first().map(String::as_str),
        Some("/new")
    );
    let clear = app.completion.entries[0].description.clone();
    assert!(clear.starts_with("Start a new conversation"), "{clear}");
}

#[test]
fn no_match_closes_the_popup() {
    let mut app = App::default();
    assert!(type_slash(&mut app, "zzqx").is_empty());
    assert!(!completion::is_open(&app));
}

#[test]
fn skills_join_the_menu_as_they_are_enabled_and_leave_when_disabled() {
    let mut app = App::default();
    app.completion.skills = vec![Skill::new("deploy-app", "Deploy the app")];
    assert_eq!(
        type_slash(&mut app, "deploy").first().map(String::as_str),
        Some("/deploy-app")
    );
    assert_eq!(app.completion.entries[0].description, "Deploy the app");

    app.completion.skills.clear();
    assert!(!type_slash(&mut app, "deploy").contains(&"/deploy-app".to_owned()));
}

fn retry_and_retro(order: [&str; 2]) -> App {
    let mut app = App::default();
    app.completion.skills = order
        .iter()
        .map(|name| Skill {
            origin: SkillOrigin::User,
            ..Skill::new(*name, format!("The {name} skill"))
        })
        .collect();
    app
}

#[test]
fn skill_rows_carry_their_origin_and_built_ins_none() {
    let mut app = retry_and_retro(["retry", "retro"]);
    type_slash(&mut app, "ret");
    let origin = |label: &str| {
        app.completion
            .entries
            .iter()
            .find(|entry| entry.label == label)
            .map(|entry| entry.origin)
    };
    assert_eq!(origin("/retro"), Some(Some(SkillOrigin::User)));
    assert_eq!(origin("/retry"), Some(None));
}

#[test]
fn a_skill_named_like_a_built_in_is_left_out_since_the_built_in_runs() {
    let mut app = retry_and_retro(["retry", "retro"]);
    let labels = type_slash(&mut app, "re");
    assert_eq!(labels.iter().filter(|label| *label == "/retry").count(), 1);
    assert_eq!(
        classify("/retry", &app.completion.skills),
        ClassifiedInput::SlashCommand { command: "/retry" }
    );
    app.completion
        .skills
        .push(Skill::new("clean", "Shadowed by /clear"));
    assert!(!type_slash(&mut app, "clean").contains(&"/clean".to_owned()));
}

#[test]
fn a_shadowed_skill_stays_reachable_as_a_mid_prompt_mention() {
    let mut app = retry_and_retro(["retry", "retro"]);
    app.chat_input.input = "please /retr".into();
    app.chat_input.cursor = app.chat_input.input.len();
    completion::input_changed(&mut app);
    let retry = app.completion.entries.iter().find(|e| e.label == "/retry");
    assert_eq!(
        retry.map(|entry| entry.origin),
        Some(Some(SkillOrigin::User))
    );
}

#[test]
fn retry_and_retro_rank_the_same_whatever_the_skill_order() {
    for order in [["retry", "retro"], ["retro", "retry"]] {
        for (query, first) in [
            ("ret", "/retro"),
            ("retr", "/retro"),
            ("retro", "/retro"),
            ("retry", "/retry"),
        ] {
            let mut app = retry_and_retro(order);
            let labels = type_slash(&mut app, query);
            assert_eq!(
                labels.first().map(String::as_str),
                Some(first),
                "{order:?} {query}"
            );
        }
        let mut app = retry_and_retro(order);
        assert_eq!(
            type_slash(&mut app, "retr"),
            ["/retro", "/retry", "/remote-project"]
        );
    }
}

#[test]
fn tab_and_enter_accept_the_highlighted_row() {
    for (moves, accepted) in [(0, "retro"), (1, "retry"), (3, "retro")] {
        let mut app = retry_and_retro(["retry", "retro"]);
        type_slash(&mut app, "retr");
        for _ in 0..moves {
            completion::navigate(&mut app, true);
        }
        completion::tab(&mut app);
        assert_eq!(
            app.chat_input.input,
            format!("{accepted} "),
            "{moves} moves"
        );
    }
    let mut app = retry_and_retro(["retry", "retro"]);
    type_slash(&mut app, "retr");
    assert!(completion::accept(&mut app));
    assert_eq!(
        classify(
            &format!("/{}", app.chat_input.input),
            &app.completion.skills
        ),
        ClassifiedInput::Skill {
            command: "/retro".into(),
            name: "retro".into()
        }
    );
}
