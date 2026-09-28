//! Regression: Ctrl-C hit the app-server mid-shutdown and printed a traceback.

use std::time::Duration;

use vibe_rs::server::{Client, Launch};

fn helper_launch(test: &str) -> Launch {
    Launch {
        program: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            test.into(),
            "--nocapture".into(),
        ],
        cwd: None,
    }
}

#[cfg(unix)]
fn grandchild_pidfile(parent: u32) -> std::path::PathBuf {
    std::env::temp_dir().join(format!("vibe-rs-grandchild-{parent}"))
}

#[cfg(unix)]
fn process_running(pid: i32) -> bool {
    unsafe { libc::kill(pid, 0) == 0 }
}

#[cfg(unix)]
async fn wait_for_grandchild(parent: u32) -> i32 {
    let path = grandchild_pidfile(parent);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
    loop {
        if let Ok(text) = std::fs::read_to_string(&path) {
            if let Ok(pid) = text.trim().parse::<i32>() {
                return pid;
            }
        }
        if tokio::time::Instant::now() >= deadline {
            panic!("grandchild pidfile {path:?} was not written");
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

#[cfg(unix)]
async fn wait_until_stopped(pid: i32) {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
    while process_running(pid) {
        if tokio::time::Instant::now() >= deadline {
            panic!("pid {pid} still running after group kill");
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

#[cfg(unix)]
#[test]
#[ignore]
fn helper_spawns_grandchild_and_hangs() {
    let grandchild = std::process::Command::new("sleep")
        .arg("3600")
        .spawn()
        .expect("spawn grandchild");
    std::fs::write(
        grandchild_pidfile(std::process::id()),
        grandchild.id().to_string(),
    )
    .expect("write grandchild pid");
    // Intentionally never reaped: the group kill under test must reach it.
    std::mem::forget(grandchild);
    loop {
        std::thread::sleep(Duration::from_secs(3600));
    }
}

#[cfg(unix)]
#[tokio::test]
async fn app_server_runs_outside_the_tui_process_group() {
    let launch = Launch {
        program: "sleep".into(),
        args: vec!["30".into()],
        cwd: None,
    };
    let (_client, child, _notifications, _crash_rx) =
        Client::spawn(launch).await.expect("spawn backend");
    let pid = child.pid().expect("backend pid") as i32;
    let backend_group = unsafe { libc::getpgid(pid) };
    let tui_group = unsafe { libc::getpgid(0) };
    assert!(backend_group > 0 && tui_group > 0, "getpgid failed");
    assert_ne!(backend_group, tui_group);
}

#[cfg(unix)]
#[test]
#[ignore]
fn helper_exits_leaving_grandchild() {
    let grandchild = std::process::Command::new("sleep")
        .arg("3600")
        .spawn()
        .expect("spawn grandchild");
    std::fs::write(
        grandchild_pidfile(std::process::id()),
        grandchild.id().to_string(),
    )
    .expect("write grandchild pid");
    // Intentionally never reaped: the leader exits, the group kill must still reach it.
    std::mem::forget(grandchild);
}

#[cfg(unix)]
#[tokio::test]
async fn normal_exit_kills_orphaned_process_group() {
    let (_client, child, _notifications, _crash_rx) =
        Client::spawn(helper_launch("helper_exits_leaving_grandchild"))
            .await
            .expect("spawn helper");
    let parent = child.pid().expect("backend pid");
    let grandchild = wait_for_grandchild(parent).await;
    // The leader may already be reaped by tokio's orphan reaper; the group id
    // outlives it, so compare against the leader pid (process_group(0)).
    assert_eq!(
        unsafe { libc::getpgid(grandchild) },
        parent as i32,
        "grandchild must share the backend's process group"
    );
    // Normal exit: the child is reaped, so only the cached pgid can reach the group.
    assert!(child.wait_with_grace().await.is_some());
    wait_until_stopped(grandchild).await;
    let _ = std::fs::remove_file(grandchild_pidfile(parent));
}

#[cfg(unix)]
#[tokio::test]
async fn grace_kill_reaps_the_backend_process_group() {
    let (_client, child, _notifications, _crash_rx) =
        Client::spawn(helper_launch("helper_spawns_grandchild_and_hangs"))
            .await
            .expect("spawn helper");
    let parent = child.pid().expect("backend pid");
    let grandchild = wait_for_grandchild(parent).await;
    assert_eq!(
        unsafe { libc::getpgid(parent as i32) },
        unsafe { libc::getpgid(grandchild) },
        "grandchild must share the backend's process group"
    );
    assert!(child.wait_with_grace().await.is_none());
    wait_until_stopped(grandchild).await;
    let _ = std::fs::remove_file(grandchild_pidfile(parent));
}

#[cfg(unix)]
#[tokio::test]
async fn drop_kills_the_backend_process_group() {
    let (_client, child, _notifications, _crash_rx) =
        Client::spawn(helper_launch("helper_spawns_grandchild_and_hangs"))
            .await
            .expect("spawn helper");
    let parent = child.pid().expect("backend pid");
    let grandchild = wait_for_grandchild(parent).await;
    drop(child);
    wait_until_stopped(grandchild).await;
    let _ = std::fs::remove_file(grandchild_pidfile(parent));
}
