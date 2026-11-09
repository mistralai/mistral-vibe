//! Backend process-group isolation and teardown, observed through pipe EOFs instead of timers.
#![cfg(unix)]

use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::time::Duration;

use vibe_rs::server::child::ChildHandle;
use vibe_rs::server::{Client, Launch};

const HELPER_FD: &str = "VIBE_TEST_HELPER_ALIVE_FD";
const GROUP_FD: &str = "VIBE_TEST_GROUP_ALIVE_FD";
const HANG_GUARD: Duration = Duration::from_secs(60);

struct Helper {
    _client: Client,
    child: ChildHandle,
    alive: File,
    group: BufReader<File>,
    grandchild: i32,
}

fn pipe() -> (File, OwnedFd) {
    let mut fds = [0; 2];
    assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0, "pipe failed");
    unsafe { libc::fcntl(fds[0], libc::F_SETFD, libc::FD_CLOEXEC) };
    unsafe { (File::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) }
}

// A detached thread, not spawn_blocking: runtime shutdown would join a read that never ends.
async fn blocking<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> T {
    let (tx, rx) = tokio::sync::oneshot::channel();
    std::thread::spawn(move || tx.send(work()));
    tokio::time::timeout(HANG_GUARD, rx)
        .await
        .expect("process outlived teardown")
        .expect("pipe reader thread")
}

async fn read_to_eof(mut pipe: impl Read + Send + 'static) {
    blocking(move || pipe.read_to_end(&mut Vec::new()).expect("read pipe")).await;
}

async fn spawn_helper(test: &str) -> Helper {
    let (alive, alive_w) = pipe();
    let (group, group_w) = pipe();
    std::env::set_var(HELPER_FD, alive_w.as_raw_fd().to_string());
    std::env::set_var(GROUP_FD, group_w.as_raw_fd().to_string());
    let launch = Launch {
        program: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            test_name!(test),
            "--nocapture".into(),
        ],
        cwd: None,
    };
    let (_client, child, _notifications, _crash_rx) =
        Client::spawn(launch).await.expect("spawn helper");
    drop((alive_w, group_w));
    let (group, grandchild) = blocking(move || {
        let mut group = BufReader::new(group);
        let mut line = String::new();
        group.read_line(&mut line).expect("read grandchild pid");
        (group, line.trim().parse().expect("grandchild pid"))
    })
    .await;
    Helper {
        _client,
        child,
        alive,
        group,
        grandchild,
    }
}

fn spawn_grandchild() {
    let fd = |key: &str| -> i32 { std::env::var(key).expect(key).parse().expect(key) };
    unsafe { libc::fcntl(fd(HELPER_FD), libc::F_SETFD, libc::FD_CLOEXEC) };
    let grandchild = std::process::Command::new("sleep")
        .arg("3600")
        .spawn()
        .expect("spawn grandchild");
    let mut group = unsafe { File::from_raw_fd(fd(GROUP_FD)) };
    writeln!(group, "{}", grandchild.id()).expect("write grandchild pid");
    // Intentionally never reaped: only the group kill under test may stop it.
    std::mem::forget(grandchild);
}

#[test]
#[ignore]
fn helper_spawns_grandchild_and_hangs() {
    spawn_grandchild();
    loop {
        std::thread::sleep(Duration::from_secs(3600));
    }
}

#[test]
#[ignore]
fn helper_exits_leaving_grandchild() {
    spawn_grandchild();
}

// One sequential test: a concurrent spawn would inherit the pipe write ends and delay EOF.
#[tokio::test]
async fn backend_process_group_is_isolated_and_torn_down() {
    let grace = spawn_helper("helper_spawns_grandchild_and_hangs").await;
    let pid = grace.child.pid().expect("backend pid") as i32;
    let backend_group = unsafe { libc::getpgid(pid) };
    assert!(backend_group > 0, "getpgid failed");
    assert_ne!(backend_group, unsafe { libc::getpgid(0) });
    assert_eq!(unsafe { libc::getpgid(grace.grandchild) }, backend_group);
    assert!(grace.child.wait_with_grace().await.is_none());
    read_to_eof(grace.group).await;

    let dropped = spawn_helper("helper_spawns_grandchild_and_hangs").await;
    drop(dropped.child);
    read_to_eof(dropped.group).await;

    let exited = spawn_helper("helper_exits_leaving_grandchild").await;
    let leader = exited.child.pid().expect("backend pid") as i32;
    read_to_eof(exited.alive).await;
    assert_eq!(unsafe { libc::getpgid(exited.grandchild) }, leader);
    assert!(exited.child.wait_with_grace().await.is_some());
    read_to_eof(exited.group).await;
}
