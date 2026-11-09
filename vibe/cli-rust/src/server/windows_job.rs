//! Windows app-server isolation: assign a suspended child before it can spawn tools.

use std::io;
use std::mem::{offset_of, size_of};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::ptr::null;

use anyhow::{bail, ensure, Context, Result};
use tokio::process::{Child, Command};
use windows_sys::Win32::Foundation::{ERROR_NO_MORE_FILES, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{
    GetProcessIdOfThread, OpenThread, ResumeThread, CREATE_NEW_PROCESS_GROUP, CREATE_SUSPENDED,
    THREAD_QUERY_LIMITED_INFORMATION, THREAD_SUSPEND_RESUME,
};

pub(super) struct WindowsJob(OwnedHandle);

impl WindowsJob {
    pub(super) fn spawn(cmd: &mut Command) -> Result<(Child, Self)> {
        let job = Self::new().context("create app-server job")?;
        // Ctrl+C stays with the TUI; the child cannot execute before job assignment.
        cmd.creation_flags(CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED)
            .kill_on_drop(true);
        let child = cmd.spawn().context("spawn suspended app-server")?;
        let process = child
            .raw_handle()
            .context("suspended child handle missing")?;
        // SAFETY: both handles remain owned for the duration of this call.
        if unsafe { AssignProcessToJobObject(job.0.as_raw_handle(), process) } == 0 {
            return Err(io::Error::last_os_error()).context("assign app-server to job");
        }
        let thread = initial_thread(child.id().context("suspended child pid missing")?)?;
        // SAFETY: this is the owned initial-thread handle, opened with resume rights.
        match unsafe { ResumeThread(thread.as_raw_handle()) } {
            1 => Ok((child, job)),
            u32::MAX => Err(io::Error::last_os_error()).context("resume app-server thread"),
            count => bail!("unexpected app-server thread suspend count: {count}"),
        }
        // On every failure, kill_on_drop kills the leader and job closure kills its tree.
    }

    fn new() -> Result<Self> {
        // SAFETY: null attributes create a non-inheritable handle; null name is private.
        let handle = unsafe { CreateJobObjectW(null(), null()) };
        if handle.is_null() {
            return Err(io::Error::last_os_error().into());
        }
        // SAFETY: CreateJobObjectW returned a fresh, valid handle owned here.
        let job = Self(unsafe { OwnedHandle::from_raw_handle(handle) });
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: the pointer and byte count describe the initialized limits structure.
        if unsafe {
            SetInformationJobObject(
                job.0.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } == 0
        {
            return Err(io::Error::last_os_error()).context("set kill-on-close job limit");
        }
        Ok(job)
    }
}

fn initial_thread(pid: u32) -> Result<OwnedHandle> {
    // SAFETY: thread-only snapshots need no process handle or module access.
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error()).context("snapshot app-server thread");
    }
    // SAFETY: a successful snapshot is a fresh handle owned here.
    let snapshot = unsafe { OwnedHandle::from_raw_handle(snapshot) };
    let mut entry = THREADENTRY32 {
        dwSize: size_of::<THREADENTRY32>() as u32,
        ..Default::default()
    };
    let mut thread_id = None;
    // SAFETY: the writable structure has its required size field initialized.
    if unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) } == 0 {
        return Err(io::Error::last_os_error()).context("read first thread");
    }
    loop {
        ensure!(
            entry.dwSize as usize
                >= offset_of!(THREADENTRY32, th32OwnerProcessID) + size_of::<u32>(),
            "incomplete app-server thread entry"
        );
        if entry.th32OwnerProcessID == pid {
            // Fail closed rather than guess if another thread has appeared before resume.
            ensure!(
                thread_id.is_none(),
                "multiple threads in suspended app-server"
            );
            thread_id = Some(entry.th32ThreadID);
        }
        entry.dwSize = size_of::<THREADENTRY32>() as u32;
        // SAFETY: the snapshot and writable entry remain valid throughout enumeration.
        if unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) } == 0 {
            let error = io::Error::last_os_error();
            ensure!(
                error.raw_os_error() == Some(ERROR_NO_MORE_FILES as i32),
                "enumerate app-server thread: {error}"
            );
            break;
        }
    }
    let thread_id = thread_id.context("suspended app-server thread not found")?;
    // SAFETY: OpenThread validates the id; no inheritable handle is requested.
    let thread = unsafe {
        OpenThread(
            THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION,
            0,
            thread_id,
        )
    };
    if thread.is_null() {
        return Err(io::Error::last_os_error()).context("open app-server thread");
    }
    // SAFETY: OpenThread returned a fresh handle owned here.
    let thread = unsafe { OwnedHandle::from_raw_handle(thread) };
    // SAFETY: the live thread handle has query rights; recheck ownership after the snapshot.
    ensure!(
        unsafe { GetProcessIdOfThread(thread.as_raw_handle()) } == pid,
        "app-server thread owner changed"
    );
    Ok(thread)
}
