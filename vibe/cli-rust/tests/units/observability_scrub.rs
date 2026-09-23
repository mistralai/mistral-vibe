//! Path scrubbing and noise filtering for Sentry payloads.
//!
//! Expectations are the output of the Python `scrub_paths` on the same input,
//! captured by running both over a shared corpus.

use sentry::protocol::{Event, Exception, Frame, Stacktrace};
use vibe_rs::observability::scrub::scrub_paths;
use vibe_rs::observability::sentry::{is_environmental, scrub_event};

#[test]
fn keeps_the_basename_of_a_posix_path() {
    assert_eq!(
        scrub_paths("failed to open /Users/paul/code/vibe/main.rs"),
        "failed to open [Filtered]/main.rs"
    );
}

#[test]
fn collapses_a_bare_home_root() {
    assert_eq!(scrub_paths("/Users/bob"), "[Filtered]");
    assert_eq!(scrub_paths("/home/paul"), "[Filtered]");
    assert_eq!(scrub_paths("/Users/bob/"), "[Filtered]/");
}

/// The lookbehind is `(?<![\w\\/])`, so a path glued to `=` or `:` still matches.
#[test]
fn scrubs_a_path_glued_to_a_key() {
    assert_eq!(scrub_paths("cwd=/Users/paul"), "cwd=[Filtered]");
    assert_eq!(scrub_paths("HOME=/Users/johndoe"), "HOME=[Filtered]");
    assert_eq!(
        scrub_paths("path:/Users/me/app.py"),
        "path:[Filtered]/app.py"
    );
}

#[test]
fn collapses_a_windows_home_root() {
    assert_eq!(scrub_paths(r"C:\Users\bob"), "[Filtered]");
    assert_eq!(
        scrub_paths(r"C:\Users\paul\vibe.log"),
        "[Filtered]/vibe.log"
    );
}

/// Components may contain spaces, which whitespace tokenizing would split.
#[test]
fn scrubs_a_path_containing_spaces() {
    assert_eq!(
        scrub_paths("/Users/john doe/file.txt"),
        "[Filtered]/file.txt"
    );
    assert_eq!(
        scrub_paths("~/Documents/My Files/report.pdf"),
        "[Filtered]/report.pdf"
    );
}

/// A path must not start mid-word, so `foo/Users/bob` is not a match.
#[test]
fn leaves_non_paths_alone() {
    assert_eq!(
        scrub_paths("connection reset by peer"),
        "connection reset by peer"
    );
    assert_eq!(scrub_paths("foo/Users/bob"), "foo/Users/bob");
    assert_eq!(scrub_paths("a/b/c"), "a/b/c");
}

#[test]
fn scrubs_each_quoted_token_separately() {
    assert_eq!(
        scrub_paths("copy '/tmp/a.txt' to '/tmp/b.txt'"),
        "copy '[Filtered]/a.txt' to '[Filtered]/b.txt'"
    );
}

/// EPIPE, EIO and ENOSPC, matched by kind rather than by localizable text.
#[test]
fn treats_terminal_and_disk_faults_as_environmental() {
    for code in [32, 5, 28] {
        let error =
            anyhow::Error::from(std::io::Error::from_raw_os_error(code)).context("draw frame");
        assert!(is_environmental(&error), "errno {code} should be filtered");
    }
}

#[test]
fn reports_real_faults() {
    assert!(!is_environmental(&anyhow::anyhow!("protocol error")));
    let not_found = anyhow::Error::from(std::io::Error::from_raw_os_error(2));
    assert!(!is_environmental(&not_found));
}

/// `attach_stacktrace` puts local paths in frames, so the walk must reach them.
#[test]
fn scrubs_stack_frame_paths() {
    let frame = Frame {
        abs_path: Some("/Users/paul/code/vibe/src/app.rs".to_owned()),
        filename: Some("/Users/paul/code/vibe/src/app.rs".to_owned()),
        ..Default::default()
    };
    let mut event = Event {
        exception: vec![Exception {
            ty: "panic".to_owned(),
            value: Some("boom at /Users/paul/x.rs".to_owned()),
            stacktrace: Some(Stacktrace {
                frames: vec![frame],
                ..Default::default()
            }),
            ..Default::default()
        }]
        .into(),
        ..Default::default()
    };

    scrub_event(&mut event);

    let exception = &event.exception[0];
    assert_eq!(exception.value.as_deref(), Some("boom at [Filtered]/x.rs"));
    let frame = &exception.stacktrace.as_ref().unwrap().frames[0];
    assert_eq!(frame.abs_path.as_deref(), Some("[Filtered]/app.rs"));
    assert_eq!(frame.filename.as_deref(), Some("[Filtered]/app.rs"));
}

#[test]
fn drops_breadcrumbs_and_the_user_ip() {
    let mut event = Event {
        user: Some(sentry::protocol::User {
            ip_address: Some(sentry::protocol::IpAddress::Exact(
                "1.2.3.4".parse().unwrap(),
            )),
            ..Default::default()
        }),
        ..Default::default()
    };
    event.breadcrumbs.values.push(Default::default());

    scrub_event(&mut event);

    assert!(event.breadcrumbs.values.is_empty());
    assert!(event.user.unwrap().ip_address.is_none());
}
