//! `--workdir` parsing, validation, and process-directory behavior.

use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};

use clap::Parser;
use vibe_rs::cli::{resolve_user_path, workdir_to_string, Cli};
use vibe_rs::startup::resolve_launch_cwd;

struct RestoreCwd(PathBuf);

impl Drop for RestoreCwd {
    fn drop(&mut self) {
        std::env::set_current_dir(&self.0).expect("restore current directory");
    }
}

struct RestoreEnv(&'static str, Option<OsString>);

impl Drop for RestoreEnv {
    fn drop(&mut self) {
        match self.1.take() {
            Some(value) => std::env::set_var(self.0, value),
            None => std::env::remove_var(self.0),
        }
    }
}

fn test_path(name: &str) -> PathBuf {
    std::env::temp_dir().join(format!("vibe-rs-workdir-{}-{name}", std::process::id()))
}

fn cli_with_workdir(path: &Path) -> Cli {
    Cli::parse_from([
        OsString::from("vibe-rs"),
        OsString::from("--workdir"),
        path.as_os_str().to_owned(),
    ])
}

#[test]
fn workdir_flag_maps_to_cwd() {
    let cli = Cli::parse_from(["vibe-rs", "--workdir", "."]);
    assert_eq!(cli.cwd, Some(PathBuf::from(".")));
}

#[test]
fn vibe_cwd_maps_to_workdir() {
    let _restore = RestoreEnv("VIBE_CWD", std::env::var_os("VIBE_CWD"));
    std::env::set_var("VIBE_CWD", "/configured/workdir");

    let cli = Cli::parse_from(["vibe-rs"]);

    assert_eq!(cli.cwd, Some(PathBuf::from("/configured/workdir")));
}

#[test]
fn workdir_must_exist() {
    let path = test_path("missing");

    let error = cli_with_workdir(&path).change_workdir().unwrap_err();

    assert!(
        error
            .to_string()
            .contains("--workdir does not exist or is not a directory"),
        "{error:#}"
    );
}

#[test]
fn workdir_must_be_a_directory() {
    let path = test_path("file");
    std::fs::write(&path, "not a directory").expect("create test file");

    let error = cli_with_workdir(&path).change_workdir().unwrap_err();

    assert!(
        error
            .to_string()
            .contains("--workdir does not exist or is not a directory"),
        "{error:#}"
    );
    std::fs::remove_file(path).expect("remove test file");
}

#[test]
fn workdir_changes_the_process_current_directory() {
    let original = std::env::current_dir().expect("read current directory");
    let restore = RestoreCwd(original);
    let path = test_path("directory");
    std::fs::create_dir_all(&path).expect("create test directory");

    let resolved = cli_with_workdir(&path)
        .change_workdir()
        .expect("change working directory");

    assert_eq!(
        resolved,
        path.canonicalize().expect("canonicalize test path")
    );
    drop(restore);
    std::fs::remove_dir(path).expect("remove test directory");
}

#[cfg(unix)]
#[test]
fn workdir_rejects_a_non_unicode_path() {
    use std::os::unix::ffi::OsStringExt;

    let path = PathBuf::from(OsString::from_vec(vec![b'/', b't', b'm', b'p', b'/', 0xff]));

    let error = workdir_to_string(&path).unwrap_err();

    assert!(error.to_string().contains("not valid UTF-8"), "{error:#}");
}

#[test]
fn workdir_expands_home_and_rejects_an_unresolved_home() {
    let home = Path::new("/home/vibe");
    let requested = PathBuf::from("~").join("project");

    assert_eq!(
        resolve_user_path(&requested, Some(home)).expect("expand home"),
        home.join("project")
    );
    assert!(resolve_user_path(&requested, None).is_err());
    assert_eq!(
        resolve_user_path(Path::new("project"), None).expect("keep relative path"),
        PathBuf::from("project")
    );
}

#[test]
fn app_server_launch_cwd_stays_anchored_to_the_invocation_directory() {
    let invocation = Path::new("/vibe/source");

    assert_eq!(resolve_launch_cwd(invocation, None), invocation);
    assert_eq!(
        resolve_launch_cwd(invocation, Some(OsStr::new("."))),
        invocation.join(".")
    );
    assert_eq!(
        resolve_launch_cwd(invocation, Some(OsStr::new("server"))),
        invocation.join("server")
    );
    let absolute = std::env::temp_dir().join("vibe-app-server");
    assert_eq!(
        resolve_launch_cwd(invocation, Some(absolute.as_os_str())),
        absolute
    );
}
