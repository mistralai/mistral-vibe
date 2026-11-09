//! Supervisor side: runs the CLI as a foreground job of its own session.

use std::fs::File;
use std::io::{Read, Write};
use std::os::fd::FromRawFd;
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};

pub const CLI_BIN_ENV: &str = "VIBE_TEST_CLI_BIN";
pub const EVENT_FD_ENV: &str = "VIBE_TEST_EVENT_FD";
pub const CONTROL_FD_ENV: &str = "VIBE_TEST_CONTROL_FD";

/// One relayed event: a tag byte, then the fixed-size payload. Resumes are
/// not relayed: waitpid(WCONTINUED) is unreliable across platforms, so the
/// driver anchors a resume on the CLI's first post-SIGCONT repaint.
#[derive(Debug)]
pub enum Event {
    /// The CLI spawned; its pid follows.
    Started(i32),
    /// The CLI stopped (SIGTSTP or SIGTTIN).
    Stopped,
    /// The CLI exited; the raw waitpid status follows.
    Exited(i32),
}

impl Event {
    pub fn encode(&self) -> Vec<u8> {
        match self {
            Self::Started(pid) => once(b'P', pid.to_ne_bytes()),
            Self::Stopped => vec![b'S'],
            Self::Exited(status) => once(b'E', status.to_ne_bytes()),
        }
    }

    pub fn decode(stream: &mut Vec<u8>) -> Option<Self> {
        let (tag, len) = match stream.first() {
            Some(b'P') => (b'P', 5),
            Some(b'S') => (b'S', 1),
            Some(b'E') => (b'E', 5),
            _ => return None,
        };
        if stream.len() < len {
            return None;
        }
        let value = if len == 5 {
            let payload: [u8; 4] = stream[1..5].try_into().expect("payload");
            i32::from_ne_bytes(payload)
        } else {
            0
        };
        stream.drain(..len);
        Some(match tag {
            b'P' => Self::Started(value),
            b'S' => Self::Stopped,
            _ => Self::Exited(value),
        })
    }
}

fn once(tag: u8, payload: [u8; 4]) -> Vec<u8> {
    let mut frame = Vec::with_capacity(5);
    frame.push(tag);
    frame.extend_from_slice(&payload);
    frame
}

/// `#[ignore]`d supervisor entry; spawned by the driver via `current_exe`.
pub fn supervise() -> ! {
    let cli = std::env::var(CLI_BIN_ENV).expect(CLI_BIN_ENV);
    let fd: i32 = std::env::var(EVENT_FD_ENV)
        .expect(EVENT_FD_ENV)
        .parse()
        .expect("event fd number");
    // The CLI must not inherit the relay pipe: the driver reads it.
    unsafe {
        libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC);
    }
    let mut report = unsafe { File::from_raw_fd(fd) };
    let mut command = Command::new(&cli);
    command
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .process_group(0);
    // The CLI must see the default SIGTTOU/SIGTTIN dispositions: a `bg`
    // resume relies on them to stop the CLI again on its next terminal call.
    // SIG_IGN survives exec, so a launcher that ignores the job-control
    // signals would otherwise leave the CLI unable to re-stop; a real
    // terminal always starts the CLI with the defaults.
    unsafe {
        command.pre_exec(|| {
            let dfl: libc::sigaction = std::mem::zeroed();
            libc::sigaction(libc::SIGTTOU, &dfl, std::ptr::null_mut());
            libc::sigaction(libc::SIGTTIN, &dfl, std::ptr::null_mut());
            Ok(())
        });
    }
    let child = command.spawn().expect("spawn cli");
    let pid = child.id() as i32;
    // Foreground group of our controlling terminal. A SIGTTIN stop from the
    // pre-handoff window gets undone, but a never-stopped CLI must not get a
    // SIGCONT: waitpid reports it as WCONTINUED and the driver would misread it.
    unsafe {
        let _ = libc::tcsetpgrp(0, libc::getpgid(pid));
        let mut status: i32 = 0;
        if libc::waitpid(pid, &mut status, libc::WNOHANG | libc::WUNTRACED) == pid
            && libc::WIFSTOPPED(status)
        {
            libc::kill(pid, libc::SIGCONT);
        }
    }
    if let Some(fd) = std::env::var(CONTROL_FD_ENV)
        .ok()
        .and_then(|fd| fd.parse::<i32>().ok())
    {
        // The CLI must not inherit the control pipe's read end: a supervisor
        // death would then leave `job` writes silently swallowed.
        unsafe {
            libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC);
        }
        std::thread::spawn(move || {
            let control = unsafe { File::from_raw_fd(fd) };
            control_loop(control, pid);
        });
    }
    let _ = report.write_all(&Event::Started(pid).encode());
    loop {
        let mut status: i32 = 0;
        let waited = unsafe { libc::waitpid(pid, &mut status, libc::WUNTRACED) };
        if waited != pid {
            break;
        }
        if libc::WIFSTOPPED(status) {
            if report.write_all(&Event::Stopped.encode()).is_err() {
                break;
            }
        } else {
            let _ = report.write_all(&Event::Exited(status).encode());
            break;
        }
    }
    // A broken relay (driver gone) must not leave the CLI running.
    unsafe {
        libc::kill(pid, libc::SIGKILL);
        libc::kill(-pid, libc::SIGKILL);
    }
    std::process::exit(0);
}

/// Drive the terminal's foreground group while `supervise` blocks in waitpid:
/// `b` backgrounds the CLI (like shell `bg`: SIGCONT, tty stays with us),
/// `f` foregrounds it (like `fg`).
fn control_loop(control: File, pid: i32) {
    // Job control like a shell: block SIGTTOU/SIGTTIN first, or this very
    // loop's tcsetpgrp stops the supervisor as a background group member.
    unsafe {
        let mut mask: libc::sigset_t = std::mem::zeroed();
        libc::sigemptyset(&mut mask);
        libc::sigaddset(&mut mask, libc::SIGTTOU);
        libc::sigaddset(&mut mask, libc::SIGTTIN);
        libc::pthread_sigmask(libc::SIG_BLOCK, &mask, std::ptr::null_mut());
    }
    let mut control = control;
    let mut command = [0; 1];
    loop {
        match control.read(&mut command) {
            Ok(0) => return,
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => return,
        }
        match command[0] {
            b'b' => unsafe {
                // Like shell `bg`: the terminal's foreground group leaves the
                // job, and its next terminal access (tcsetattr in resume, then
                // the tty read) stops it again by SIGTTOU/SIGTTIN.
                let _ = libc::tcsetpgrp(0, libc::getpgid(0));
                libc::kill(pid, libc::SIGCONT);
            },
            b'f' => unsafe {
                let _ = libc::tcsetpgrp(0, libc::getpgid(pid));
                libc::kill(pid, libc::SIGCONT);
            },
            _ => {}
        }
    }
}
