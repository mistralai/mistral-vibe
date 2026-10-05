//! Native Windows app-server tree cleanup and console-control isolation.
#![cfg(windows)]

use std::io::Read;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::os::windows::process::CommandExt;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::Duration;

use tempfile::TempDir;
use vibe_rs::server::{ChildHandle, Client, Launch, SHUTDOWN_GRACE};
use windows_sys::Win32::Foundation::{WAIT_OBJECT_0, WAIT_TIMEOUT};
use windows_sys::Win32::System::Console::{
    AllocConsole, FreeConsole, GenerateConsoleCtrlEvent, SetConsoleCtrlHandler, CTRL_C_EVENT,
};
use windows_sys::Win32::System::Threading::{
    OpenProcess, TerminateProcess, WaitForSingleObject, CREATE_NEW_PROCESS_GROUP,
    PROCESS_SYNCHRONIZE, PROCESS_TERMINATE,
};

const DEADLINE: Duration = Duration::from_secs(10);

fn helper_command(name: &str) -> Command {
    let mut command = Command::new(std::env::current_exe().expect("test executable"));
    command.args(["--ignored", "--exact", name, "--nocapture"]);
    command
}

fn mark_ready(name: &str) {
    std::fs::write(name, std::process::id().to_string()).expect("write helper pid");
}

fn sleep_until_cleanup() {
    // Bound leaks even if the implementation under test fails to kill a descendant.
    std::thread::sleep(Duration::from_secs(60));
}

#[test]
#[ignore]
fn helper_leader() {
    drop(
        helper_command("helper_descendant")
            .creation_flags(CREATE_NEW_PROCESS_GROUP)
            .stdin(Stdio::null())
            .spawn()
            .expect("spawn descendant immediately"),
    );
    std::io::stdin()
        .read_to_end(&mut Vec::new())
        .expect("stdin EOF");
}

#[test]
#[ignore]
fn helper_descendant() {
    drop(
        helper_command("helper_leaf")
            .spawn()
            .expect("spawn second-generation descendant"),
    );
    mark_ready("descendant.pid");
    sleep_until_cleanup();
}

#[test]
#[ignore]
fn helper_leaf() {
    mark_ready("leaf.pid");
    sleep_until_cleanup();
}

struct Process(OwnedHandle);

impl Process {
    fn open(pid: u32) -> Self {
        // SAFETY: OpenProcess validates the pid; this handle cannot be inherited.
        let raw = unsafe { OpenProcess(PROCESS_SYNCHRONIZE | PROCESS_TERMINATE, 0, pid) };
        assert!(
            !raw.is_null(),
            "OpenProcess({pid}): {}",
            std::io::Error::last_os_error()
        );
        // SAFETY: OpenProcess returned a fresh, valid owned handle.
        Self(unsafe { OwnedHandle::from_raw_handle(raw) })
    }

    fn running(&self) -> bool {
        // SAFETY: the owned process handle has SYNCHRONIZE access.
        let status = unsafe { WaitForSingleObject(self.0.as_raw_handle(), 0) };
        assert!(
            matches!(status, WAIT_TIMEOUT | WAIT_OBJECT_0),
            "wait failed: {status}"
        );
        status == WAIT_TIMEOUT
    }

    async fn stopped(&self) {
        tokio::time::timeout(DEADLINE, async {
            while self.running() {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("process survived tree cleanup");
    }
}

impl Drop for Process {
    fn drop(&mut self) {
        // SAFETY: emergency test cleanup uses our owned handle, never a recycled pid.
        unsafe { TerminateProcess(self.0.as_raw_handle(), 1) };
    }
}

async fn ready_process(directory: &Path, name: &str) -> Process {
    tokio::time::timeout(DEADLINE, async {
        loop {
            if let Ok(text) = std::fs::read_to_string(directory.join(name)) {
                if let Ok(pid) = text.parse() {
                    return Process::open(pid);
                }
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("helper did not become ready")
}

async fn spawn_tree() -> (Client, ChildHandle, TempDir, Vec<Process>) {
    let directory = tempfile::tempdir().expect("helper directory");
    let command = helper_command("helper_leader");
    let launch = Launch {
        program: command.get_program().to_string_lossy().into_owned(),
        args: command
            .get_args()
            .map(|arg| arg.to_string_lossy().into_owned())
            .collect(),
        cwd: Some(directory.path().to_owned()),
    };
    let (client, child, _notifications, _crash) =
        Client::spawn(launch).await.expect("spawn backend");
    let processes = vec![
        Process::open(child.pid().expect("leader pid")),
        ready_process(directory.path(), "descendant.pid").await,
        ready_process(directory.path(), "leaf.pid").await,
    ];
    assert!(processes.iter().all(Process::running));
    (client, child, directory, processes)
}

#[tokio::test]
async fn drop_kills_leader_and_both_descendant_generations() {
    let (_client, child, _directory, processes) = spawn_tree().await;
    drop(child);
    for process in processes {
        process.stopped().await;
    }
}

#[tokio::test]
async fn normal_leader_exit_still_kills_descendants() {
    let (client, child, _directory, processes) = spawn_tree().await;
    client.close_stdin().await;
    let status = child
        .wait_with_grace()
        .await
        .expect("leader exited normally");
    assert!(status.success());
    for process in processes {
        process.stopped().await;
    }
}

#[tokio::test]
async fn grace_timeout_kills_the_whole_tree() {
    let (_client, child, _directory, processes) = spawn_tree().await;
    let started = tokio::time::Instant::now();
    assert!(
        tokio::time::timeout(SHUTDOWN_GRACE + DEADLINE, child.wait_with_grace())
            .await
            .expect("grace wait hung")
            .is_none()
    );
    assert!(started.elapsed() >= SHUTDOWN_GRACE);
    for process in processes {
        process.stopped().await;
    }
}

#[tokio::test]
async fn cancelling_grace_wait_kills_the_whole_tree() {
    let (_client, child, _directory, processes) = spawn_tree().await;
    assert!(
        tokio::time::timeout(Duration::from_millis(20), child.wait_with_grace())
            .await
            .is_err()
    );
    for process in processes {
        process.stopped().await;
    }
}

#[test]
#[ignore]
fn helper_ctrl_c_probe() {
    // SAFETY: only this disposable probe's default Ctrl+C handling is restored.
    assert_ne!(unsafe { SetConsoleCtrlHandler(None, 0) }, 0);
    mark_ready("probe.pid");
    sleep_until_cleanup();
}

unsafe extern "system" fn handle_ctrl_c(event: u32) -> i32 {
    i32::from(event == CTRL_C_EVENT)
}

#[tokio::test]
#[ignore]
async fn helper_isolated_console() {
    // SAFETY: this disposable helper detaches before creating its private console.
    unsafe {
        FreeConsole();
        assert_ne!(AllocConsole(), 0);
        assert_ne!(SetConsoleCtrlHandler(None, 0), 0);
        assert_ne!(SetConsoleCtrlHandler(Some(handle_ctrl_c), 1), 0);
    }
    let (_client, child, directory, processes) = spawn_tree().await;
    let mut probe = helper_command("helper_ctrl_c_probe")
        .current_dir(directory.path())
        .spawn()
        .expect("spawn unisolated control probe");
    let probe_handle = Process::open(probe.id());
    let _ready = ready_process(directory.path(), "probe.pid").await;
    // SAFETY: the broadcast reaches only this helper's private console, never cargo's.
    assert_ne!(unsafe { GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) }, 0);
    probe_handle.stopped().await;
    assert!(!probe.wait().expect("reap control probe").success());
    assert!(
        processes.iter().all(Process::running),
        "Ctrl+C killed the app-server tree"
    );
    drop(child);
    for process in processes {
        process.stopped().await;
    }
}

#[tokio::test]
async fn ctrl_c_does_not_reach_the_app_server_tree() {
    let mut helper = tokio::process::Command::from(helper_command("helper_isolated_console"));
    let mut child = helper
        .kill_on_drop(true)
        .spawn()
        .expect("spawn console helper");
    let status = tokio::time::timeout(Duration::from_secs(45), child.wait())
        .await
        .expect("console helper timed out")
        .expect("wait for console helper");
    assert!(status.success());
}
