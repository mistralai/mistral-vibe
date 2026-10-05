//! The uv update oracle: outdated-list parsing and the install-receipt gate.

use std::path::PathBuf;

use vibe_rs::update_notifier::uv_oracle::{
    executable_matches_uv_install, git_source_from_receipt, parse_outdated_tool_list,
    receipt_exists, PROJECT_NAME,
};
use vibe_rs::update_notifier::uv_pin::blocking_pin_from_receipt;

fn receipt_path(tool_dir: &std::path::Path) -> PathBuf {
    tool_dir.join(PROJECT_NAME).join("uv-receipt.toml")
}

#[test]
fn outdated_list_reports_the_projects_latest_release() {
    let stdout = "\
pre-commit v4.6.0 [latest: 4.6.2]\n\
- pre-commit\n\
mistral-vibe v2.25.4 [latest: 2.25.5]\n\
- vibe\n\
- vibe-acp\n\
- vibe-app-server\n";
    assert_eq!(parse_outdated_tool_list(stdout), Some("2.25.5".to_owned()));
}

#[test]
fn an_up_to_date_or_unmanaged_tool_is_absent_so_no_update() {
    // `--outdated` lists only outdated tools; anything else reads as "no
    // update" for this project.
    let stdout = "pre-commit v4.6.0 [latest: 4.6.2]\n- pre-commit\n";
    assert_eq!(parse_outdated_tool_list(stdout), None);
    assert_eq!(parse_outdated_tool_list(""), None);
}

#[test]
fn a_sibling_tool_with_a_longer_name_does_not_collide() {
    let stdout = "mistral-vibe-plus v1.0.0 [latest: 9.9.9]\n- mistral-vibe-plus\n";
    assert_eq!(parse_outdated_tool_list(stdout), None);
}

#[test]
fn a_leading_v_in_the_latest_tag_is_stripped() {
    let stdout = "mistral-vibe v2.25.4 [latest: v2.25.5]\n- vibe\n";
    assert_eq!(parse_outdated_tool_list(stdout), Some("2.25.5".to_owned()));
}

#[test]
fn the_receipt_gate_follows_uvs_install_record() {
    let tool_dir = tempfile::tempdir().unwrap();
    assert!(!receipt_exists(tool_dir.path()));

    std::fs::create_dir_all(tool_dir.path().join(PROJECT_NAME)).unwrap();
    assert!(!receipt_exists(tool_dir.path()), "no receipt file yet");
    std::fs::write(receipt_path(tool_dir.path()), "[tool]\n").unwrap();
    assert!(receipt_exists(tool_dir.path()));
}

#[test]
fn only_the_recorded_install_matches_the_receipt() {
    let tool_dir = tempfile::tempdir().unwrap();
    let venv_bin = tool_dir.path().join(PROJECT_NAME).join(".venv").join("bin");
    std::fs::create_dir_all(&venv_bin).unwrap();
    let managed = venv_bin.join("vibe");
    std::fs::write(&managed, b"managed").unwrap();

    let shim_dir = tempfile::tempdir().unwrap();
    let shim = shim_dir.path().join("vibe");
    std::fs::write(&shim, b"shim").unwrap();
    // A brew, pip, or cargo binary in the same directory is not the shim.
    let other = shim_dir.path().join("brew-vibe");
    std::fs::write(&other, b"brew").unwrap();
    // A sibling tool directory with a longer name is not this install.
    let sibling = tool_dir
        .path()
        .join(format!("{PROJECT_NAME}-extra"))
        .join("vibe");
    std::fs::create_dir_all(sibling.parent().unwrap()).unwrap();
    std::fs::write(&sibling, b"sibling").unwrap();

    let receipt = format!(
        "[tool]\nentrypoints = [{{ name = \"vibe\", install-path = \"{}\" }}]\n",
        shim.display()
    );
    // The venv binary and the recorded shim entrypoint match.
    assert!(executable_matches_uv_install(
        &managed,
        tool_dir.path(),
        &receipt
    ));
    assert!(executable_matches_uv_install(
        &shim,
        tool_dir.path(),
        &receipt
    ));
    // Anything else on a machine that also carries the uv tool does not.
    assert!(!executable_matches_uv_install(
        &other,
        tool_dir.path(),
        &receipt
    ));
    assert!(!executable_matches_uv_install(
        &sibling,
        tool_dir.path(),
        &receipt
    ));
    assert!(!executable_matches_uv_install(
        &other,
        tool_dir.path(),
        "not toml"
    ));
}

#[test]
fn an_array_of_tables_receipt_layout_matches_its_install_paths() {
    let shim_dir = tempfile::tempdir().unwrap();
    let shim = shim_dir.path().join("vibe");
    std::fs::write(&shim, b"shim").unwrap();
    let other = shim_dir.path().join("brew-vibe");
    std::fs::write(&other, b"brew").unwrap();
    let tool_dir = tempfile::tempdir().unwrap();
    let receipt = format!(
        "[[tool.entrypoints]]\nname = \"vibe\"\ninstall-path = \"{}\"\n",
        shim.display()
    );
    assert!(executable_matches_uv_install(
        &shim,
        tool_dir.path(),
        &receipt
    ));
    assert!(!executable_matches_uv_install(
        &other,
        tool_dir.path(),
        &receipt
    ));
}

#[test]
fn a_git_pinned_requirement_yields_its_source_for_the_reinstall_hint() {
    let receipt = "\
[tool]\n\
requirements = [{ name = \"mistral-vibe\", git = \"https://github.com/mistralai/mistral-vibe.git\" }]\n\
entrypoints = [{ name = \"vibe\", install-path = \"/bin/vibe\", from = \"mistral-vibe\" }]\n";
    assert_eq!(
        git_source_from_receipt(receipt),
        Some("git+https://github.com/mistralai/mistral-vibe.git".to_owned())
    );
}

#[test]
fn a_registry_requirement_has_no_git_source() {
    let receipt = "requirements = [{ name = \"mistral-vibe\" }]\n";
    assert_eq!(git_source_from_receipt(receipt), None);
    assert_eq!(git_source_from_receipt("not toml"), None);
    assert_eq!(git_source_from_receipt(""), None);
}

#[test]
fn an_exact_pin_on_another_version_blocks_the_latest() {
    let receipt =
        "[tool]\nrequirements = [{ name = \"mistral-vibe\", specifier = \"==2.25.4\" }]\n";
    assert_eq!(
        blocking_pin_from_receipt(receipt, "2.25.7"),
        Some("==2.25.4".to_owned())
    );
}

#[test]
fn an_exact_pin_naming_the_latest_does_not_block() {
    // uv tool upgrade delivers exactly the pinned version.
    let receipt =
        "[tool]\nrequirements = [{ name = \"mistral-vibe\", specifier = \"==2.25.7\" }]\n";
    assert_eq!(blocking_pin_from_receipt(receipt, "2.25.7"), None);
}

#[test]
fn the_first_blocking_clause_of_a_compound_requirement_wins() {
    let receipt =
        "[tool]\nrequirements = [{ name = \"mistral-vibe\", specifier = \">=2.25, ==2.25.4\" }]\n";
    assert_eq!(
        blocking_pin_from_receipt(receipt, "2.25.7"),
        Some("==2.25.4".to_owned())
    );
    // An earlier exact clause naming the latest leaves nothing blocking.
    let satisfied =
        "[tool]\nrequirements = [{ name = \"mistral-vibe\", specifier = \"==2.25.7, >=2.25\" }]\n";
    assert_eq!(blocking_pin_from_receipt(satisfied, "2.25.7"), None);
}

#[test]
fn arbitrary_equality_pins_like_exact_equality() {
    let receipt =
        "[tool]\nrequirements = [{ name = \"mistral-vibe\", specifier = \"===2.25.4\" }]\n";
    assert_eq!(
        blocking_pin_from_receipt(receipt, "2.25.7"),
        Some("===2.25.4".to_owned())
    );
}

#[test]
fn unpinned_and_bound_only_requirements_stay_live() {
    // No specifier: an unpinned registry install.
    let unpinned = "[tool]\nrequirements = [{ name = \"mistral-vibe\" }]\n";
    assert_eq!(blocking_pin_from_receipt(unpinned, "2.25.7"), None);
    // A git requirement never reaches the oracle's answer.
    let git = "[tool]\nrequirements = [{ name = \"mistral-vibe\", git = \"https://example.com/vibe.git\" }]\n";
    assert_eq!(blocking_pin_from_receipt(git, "2.25.7"), None);
    // Lower bounds and compatible release never block provably.
    for specifier in [">=2.25.4", "~=2.25.4", "<2.26.0", "<=2.25.4", "!=2.25.8"] {
        let receipt = format!(
            "[tool]\nrequirements = [{{ name = \"mistral-vibe\", specifier = \"{specifier}\" }}]\n"
        );
        assert_eq!(blocking_pin_from_receipt(&receipt, "2.25.7"), None);
    }
}

#[test]
fn an_unparsable_bound_latest_or_receipt_blocks_nothing() {
    let receipt =
        "[tool]\nrequirements = [{ name = \"mistral-vibe\", specifier = \"==garbage\" }]\n";
    assert_eq!(blocking_pin_from_receipt(receipt, "2.25.7"), None);
    let pinned = "[tool]\nrequirements = [{ name = \"mistral-vibe\", specifier = \"==2.25.4\" }]\n";
    assert_eq!(blocking_pin_from_receipt(pinned, "not a version"), None);
    assert_eq!(blocking_pin_from_receipt("not toml", "2.25.7"), None);
}

#[test]
fn another_tools_requirement_does_not_block() {
    let receipt = "[tool]\nrequirements = [{ name = \"other-tool\", specifier = \"==1.0.0\" }]\n";
    assert_eq!(blocking_pin_from_receipt(receipt, "2.25.7"), None);
}
