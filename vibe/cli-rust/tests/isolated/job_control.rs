//! Ctrl+Z's job stop completes while another thread spawns children.

#![cfg(unix)]

use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use vibe_rs::job_control;

/// Each round races the stop against a spawn loop; before the lock a stop caught a vfork child and never completed.
#[test]
fn job_stop_completes_while_another_thread_spawns() {
    for round in 0..50 {
        let mut helper = Command::new(std::env::current_exe().expect("test executable"))
            .args([
                "--ignored",
                "--exact",
                &test_name!("helper_stop_while_spawning"),
            ])
            .stdout(Stdio::null())
            .process_group(0)
            .spawn()
            .expect("spawn helper");
        let pid = helper.id() as i32;
        let stopped = wait_stopped(pid, Duration::from_secs(5));
        unsafe { libc::kill(-pid, libc::SIGKILL) };
        let _ = helper.wait();
        assert!(stopped, "round {round}: the job stop never completed");
    }
}

fn wait_stopped(pid: i32, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        let mut status = 0;
        if unsafe { libc::waitpid(pid, &mut status, libc::WUNTRACED | libc::WNOHANG) } == pid {
            return libc::WIFSTOPPED(status);
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    false
}

/// The ignored helper: spawns in a loop on one thread and stops the job from another.
#[test]
#[ignore]
fn helper_stop_while_spawning() {
    std::thread::spawn(|| loop {
        if let Ok(mut child) = job_control::spawn(Command::new("true").stdout(Stdio::null())) {
            let _ = child.wait();
        }
    });
    std::thread::sleep(Duration::from_millis(2));
    job_control::stop_job();
}
