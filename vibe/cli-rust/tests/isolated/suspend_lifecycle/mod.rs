//! Terminal-close and kill scenarios against the suspended real CLI (VIBE-4913).

#![cfg(unix)]

mod pty;
mod session;
mod supervisor;

use session::{Session, CTRL_Z, SUSPEND_HINT};

// Scenario A: terminal close (SIGHUP) while suspended must kill the CLI; the
// app-server then dies from stdin EOF (no Drop runs on a signal death).
#[test]
fn suspended_cli_dies_on_sighup_and_reaps_app_server() {
    let mut s = Session::start();
    s.wait_session_started();
    s.send(CTRL_Z);
    s.wait_output(SUSPEND_HINT);
    s.wait_stopped();
    // A terminal close sends the foreground group SIGHUP, then SIGCONTs stopped
    // members so the signal can act; a plain kill() of the pty cannot.
    s.signal(libc::SIGHUP);
    s.signal(libc::SIGCONT);
    let exit = s.wait_exit("SIGHUP while suspended");
    assert_eq!(
        exit.signal(),
        Some(libc::SIGHUP),
        "expected death by SIGHUP, got {exit:?}"
    );
    s.wait_backend_gone();
}

// Scenario B (control): Ctrl+D quits cleanly and the server sees session/stop + EOF.
#[test]
fn ctrl_d_quits_and_stops_the_session() {
    let mut s = Session::start();
    s.wait_session_started();
    let exit = s.quit_and_exit("Ctrl+D");
    assert_eq!(exit.code(), Some(0), "expected clean exit, got {exit:?}");
    assert!(
        s.requests().contains("session/stop"),
        "server never saw session/stop"
    );
    s.wait_backend_gone();
}

// Scenario C (control): SIGTERM quits gracefully and is bounded.
#[test]
fn sigterm_quits_gracefully() {
    let mut s = Session::start();
    s.wait_session_started();
    s.signal(libc::SIGTERM);
    let exit = s.wait_exit("SIGTERM");
    assert_eq!(exit.code(), Some(0), "expected clean exit, got {exit:?}");
    assert!(
        s.requests().contains("session/stop"),
        "server never saw session/stop"
    );
    s.wait_backend_gone();
}

// Scenario D: suspend -> fg -> normal exit still works.
#[test]
fn resume_after_suspend_still_exits_cleanly() {
    let mut s = Session::start();
    s.wait_session_started();
    s.send(CTRL_Z);
    s.wait_output(SUSPEND_HINT);
    s.wait_stopped();
    s.signal(libc::SIGCONT);
    s.wait_resumed();
    let exit = s.quit_and_exit("Ctrl+D after resume");
    assert_eq!(exit.code(), Some(0), "expected clean exit, got {exit:?}");
    s.wait_backend_gone();
}

// Scenario E: after resume the signal handlers are re-armed, so a later SIGTERM
// still quits gracefully instead of hitting the restored default action.
#[test]
fn sigterm_after_resume_is_handled_again() {
    let mut s = Session::start();
    s.wait_session_started();
    s.send(CTRL_Z);
    s.wait_output(SUSPEND_HINT);
    s.wait_stopped();
    s.signal(libc::SIGCONT);
    s.wait_resumed();
    s.signal(libc::SIGTERM);
    let exit = s.wait_exit("SIGTERM after resume");
    assert_eq!(exit.code(), Some(0), "expected clean exit, got {exit:?}");
    assert!(
        s.requests().contains("session/stop"),
        "server never saw session/stop"
    );
    s.wait_backend_gone();
}

// Scenario F: shell `bg` (resume without the terminal) twice, then `fg`: the
// stop/continue cycle must not kill the CLI with an interrupted-syscall error.
#[test]
fn bg_twice_then_fg_still_exits_cleanly() {
    let mut s = Session::start();
    s.wait_session_started();
    s.send(CTRL_Z);
    s.wait_output(SUSPEND_HINT);
    s.wait_stopped();
    s.job(b'b');
    // Each `bg` stops the backgrounded CLI again on its next terminal call;
    // the second stop only happens because the interrupted call was retried.
    s.wait_stopped();
    s.job(b'b');
    s.wait_stopped();
    s.job(b'f');
    s.wait_any_output();
    let exit = s.quit_and_exit("Ctrl+D after bg/bg/fg");
    assert_eq!(exit.code(), Some(0), "expected clean exit, got {exit:?}");
    s.wait_backend_gone();
}

// Scenario G: after `bg`, the CLI re-stops with the default dispositions still
// installed, so a terminal close in that backgrounded window kills it too —
// before, the handlers were already restored by then and a close pended.
#[test]
fn terminal_close_while_backgrounded_kills_the_cli() {
    let mut s = Session::start();
    s.wait_session_started();
    s.send(CTRL_Z);
    s.wait_output(SUSPEND_HINT);
    s.wait_stopped();
    s.job(b'b');
    s.wait_stopped();
    // A terminal close sends the job SIGHUP, then SIGCONTs stopped members
    // so the signal can act; with the defaults installed it acts immediately.
    s.signal(libc::SIGHUP);
    s.signal(libc::SIGCONT);
    let exit = s.wait_exit("SIGHUP while backgrounded");
    assert_eq!(
        exit.signal(),
        Some(libc::SIGHUP),
        "expected death by SIGHUP, got {exit:?}"
    );
    s.wait_backend_gone();
}

/// The ignored supervisor entry, spawned as a process by `Session::start`.
#[test]
#[ignore]
fn helper_session_supervisor() {
    supervisor::supervise();
}
