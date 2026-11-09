//! The brew update oracle: outdated-formula parsing and the Cellar gate.

use std::path::Path;

use vibe_rs::update_notifier::brew_oracle::{
    executable_in_cellar, outdated_answer, parse_outdated_formula, PROJECT_NAME,
};

#[test]
fn outdated_formula_reports_the_brew_deliverable_latest() {
    let stdout = format!(
        r#"{{"formulae":[{{"name":"{PROJECT_NAME}","installed_versions":["2.25.4"],"current_version":"2.25.7","pinned":false,"pinned_version":null,"deps":[]}}],"casks":[]}}"#
    );
    assert_eq!(parse_outdated_formula(&stdout), Some("2.25.7".to_owned()));
}

#[test]
fn an_up_to_date_formula_is_absent_so_no_update() {
    // `brew outdated --json=v2` lists only outdated formulae.
    assert_eq!(
        parse_outdated_formula(r#"{"formulae":[],"casks":[]}"#),
        None
    );
    assert_eq!(parse_outdated_formula(""), None);
}

#[test]
fn a_sibling_formula_with_a_longer_name_does_not_collide() {
    let stdout = format!(
        r#"{{"formulae":[{{"name":"{PROJECT_NAME}-plus","current_version":"9.9.9"}}],"casks":[]}}"#
    );
    assert_eq!(parse_outdated_formula(&stdout), None);
}

#[test]
fn casks_never_answer_for_the_formula() {
    let stdout = format!(
        r#"{{"formulae":[],"casks":[{{"name":"{PROJECT_NAME}","current_version":"9.9.9"}}]}}"#
    );
    assert_eq!(parse_outdated_formula(&stdout), None);
}

#[test]
fn a_missing_or_empty_current_version_is_no_update() {
    let stdout = format!(r#"{{"formulae":[{{"name":"{PROJECT_NAME}"}}]}}"#);
    assert_eq!(parse_outdated_formula(&stdout), None);
    let stdout = format!(r#"{{"formulae":[{{"name":"{PROJECT_NAME}","current_version":""}}]}}"#);
    assert_eq!(parse_outdated_formula(&stdout), None);
}

#[test]
fn a_leading_v_in_the_current_version_is_stripped() {
    let stdout =
        format!(r#"{{"formulae":[{{"name":"{PROJECT_NAME}","current_version":"v2.25.7"}}]}}"#);
    assert_eq!(parse_outdated_formula(&stdout), Some("2.25.7".to_owned()));
}

/// Homebrew reports a formula revision as a trailing `_N`; mapped to a PEP 440
/// local segment it still orders above the bare release.
#[test]
fn a_revision_bump_is_a_real_update() {
    let revision =
        format!(r#"{{"formulae":[{{"name":"{PROJECT_NAME}","current_version":"2.25.8_1"}}]}}"#);
    assert_eq!(
        parse_outdated_formula(&revision),
        Some("2.25.8+1".to_owned())
    );
    let plain =
        format!(r#"{{"formulae":[{{"name":"{PROJECT_NAME}","current_version":"2.25.8"}}]}}"#);
    assert_eq!(parse_outdated_formula(&plain), Some("2.25.8".to_owned()));

    let bumped = vibe_rs::update_notifier::version::parse_version("2.25.8+1").unwrap();
    let bare = vibe_rs::update_notifier::version::parse_version("2.25.8").unwrap();
    assert!(bumped > bare);
    let later = vibe_rs::update_notifier::version::parse_version("2.25.9").unwrap();
    assert!(later > bumped);
}

/// Naming an outdated formula makes brew exit 1 with the JSON answer on
/// stdout; a genuine failure exits 1 with the error on stderr and no JSON.
#[test]
fn an_outdated_formulas_exit_1_still_answers() {
    let outdated = format!(
        r#"{{"formulae":[{{"name":"{PROJECT_NAME}","current_version":"2.25.7"}}],"casks":[]}}"#
    );
    let answer = outdated_answer(&outdated, "", false).unwrap();
    assert_eq!(
        answer.map(|update| update.latest_version),
        Some("2.25.7".to_owned())
    );

    let up_to_date = outdated_answer(r#"{"formulae":[],"casks":[]}"#, "", true).unwrap();
    assert_eq!(up_to_date, None);

    let error = outdated_answer("", r#"Error: No available formula with the name"#, false);
    let message = error.unwrap_err().user_message.unwrap();
    assert!(message.contains("brew could not list outdated formulae"));
    assert!(message.contains("No available formula"));
}

/// A formula executable reaches the user through a `bin` symlink into the
/// keg; a Homebrew-Python pip script is a regular file beside that `bin`.
#[test]
fn only_a_cellar_executable_is_a_brew_install() {
    let home = tempfile::tempdir().unwrap();
    let cellar = home.path().join("Cellar");
    let keg = cellar.join(PROJECT_NAME).join("2.25.7").join("bin");
    std::fs::create_dir_all(&keg).unwrap();
    std::fs::write(keg.join("vibe"), b"brew").unwrap();
    let bin = home.path().join("bin");
    std::fs::create_dir_all(&bin).unwrap();

    // A pip script: a regular file inside Homebrew's prefix, outside the
    // Cellar.
    let pip = bin.join("vibe");
    std::fs::write(&pip, b"pip").unwrap();
    assert!(!executable_in_cellar(&pip, &cellar));

    #[cfg(unix)]
    {
        // A formula executable: reached through a `bin` symlink into the keg,
        // which canonicalization resolves.
        let formula = bin.join("vibe-formula");
        std::os::unix::fs::symlink(
            Path::new("../Cellar")
                .join(PROJECT_NAME)
                .join("2.25.7")
                .join("bin")
                .join("vibe"),
            &formula,
        )
        .unwrap();
        assert!(executable_in_cellar(&formula, &cellar));
    }
}
