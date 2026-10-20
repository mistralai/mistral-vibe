//! A `vibe run-server` process over a scripted multiplexed app-server, and its client.

#![allow(dead_code)]

use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

pub const SECRET: &str = "sk-run-secret-0123456789";
/// Within the client's close acknowledgement timeout.
pub const SLOW_CLOSE: Duration = Duration::from_secs(5);

fn send_frame(frame: Value) {
    let mut stdout = std::io::stdout().lock();
    writeln!(stdout, "{frame}").unwrap();
    stdout.flush().unwrap();
}

/// By runId: `leak*` fails `initialize` quoting its credentials, `crash*` exits,
/// `wedge*` opens its session then answers nothing, others hang; `slow*`
/// acknowledges its channel's close only after `SLOW_CLOSE`, `mute*` never, and
/// then ignores the end of its input. Its pid file holds its `MALLOC_ARENA_MAX`.
pub fn fake_app_server() {
    if let Some(dir) = std::env::var_os("FAKE_PIDS") {
        let arenas = std::env::var("MALLOC_ARENA_MAX").unwrap_or_default();
        std::fs::write(
            PathBuf::from(dir).join(std::process::id().to_string()),
            arenas,
        )
        .unwrap();
    }
    let mut opens: HashMap<String, Value> = HashMap::new();
    let mut deaf = false;
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let frame: Value = serde_json::from_str(&line).unwrap();
        let channel = frame["channelId"].as_str().unwrap().to_owned();
        if let Some(open) = frame.get("open") {
            opens.insert(channel.clone(), open.clone());
        }
        let message = &frame["message"];
        let run_id = opens[&channel]["runId"].as_str().unwrap().to_owned();
        deaf |= run_id.starts_with("mute");
        if message.is_null() {
            let ack = json!({"channelId": channel, "message": null});
            if run_id.starts_with("mute") {
                continue;
            }
            if run_id.starts_with("slow") {
                std::thread::spawn(move || {
                    std::thread::sleep(SLOW_CLOSE);
                    send_frame(ack);
                });
            } else {
                send_frame(ack);
            }
            continue;
        }
        let open = &opens[&channel];
        let reply = |result: Value| {
            let reply = json!({"jsonrpc": "2.0", "id": message["id"], "result": result});
            send_frame(json!({"channelId": channel, "message": reply}));
        };
        if run_id.starts_with("wedge") {
            match message["method"].as_str() {
                Some("initialize") => reply(json!({})),
                Some("session/start") => reply(json!({"state": {"session": {"id": "wedged"}}})),
                _ => {}
            }
            continue;
        }
        if message["method"] != "initialize" {
            continue;
        }
        if run_id.starts_with("crash") {
            std::process::exit(3);
        }
        if run_id.starts_with("leak") {
            let keys: Vec<&str> = open["credentials"]
                .as_object()
                .unwrap()
                .values()
                .filter_map(Value::as_str)
                .collect();
            let error = json!({"code": "internal_error", "message": format!("bad key {}", keys.join(",")), "data": null});
            let reply = json!({"jsonrpc": "2.0", "id": message["id"], "error": error});
            send_frame(json!({"channelId": channel, "message": reply}));
        }
    }
    if deaf {
        std::thread::sleep(Duration::MAX);
    }
}

pub struct RunServer {
    child: Child,
    stdin: Option<ChildStdin>,
    lines: mpsc::Receiver<String>,
    pending: VecDeque<Value>,
    pub stdout: Vec<String>,
    pub home: PathBuf,
    pub pids: PathBuf,
    next_id: u64,
}

impl RunServer {
    /// `vibe run-server` with `args`, its app-servers the test binary's `fake`.
    pub fn start(fake: &str, args: &[&str]) -> Self {
        Self::start_with_env(fake, args, &[])
    }

    pub fn start_with_env(fake: &str, args: &[&str], env: &[(&str, &str)]) -> Self {
        let home = std::env::temp_dir().join(format!(
            "vibe-run-server-{}-{}",
            std::process::id(),
            fake_unique()
        ));
        let _ = std::fs::remove_dir_all(&home);
        let pids = home.join("pids");
        std::fs::create_dir_all(&pids).unwrap();
        let fake_cmd = format!(
            "{} --ignored --exact --nocapture -- {fake}",
            std::env::current_exe().unwrap().display()
        );
        let mut child = Command::new(env!("CARGO_BIN_EXE_vibe-rs"))
            .arg("run-server")
            .args(args)
            .env("VIBE_HOME", &home)
            .env("VIBE_APP_SERVER_CMD", fake_cmd)
            .env("FAKE_PIDS", &pids)
            .env("LOG_LEVEL", "DEBUG")
            .env_remove("VIBE_CWD")
            .env_remove("MALLOC_ARENA_MAX")
            .envs(env.iter().copied())
            .current_dir(&home)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let stdout = child.stdout.take().unwrap();
        let (tx, lines) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        Self {
            stdin: child.stdin.take(),
            child,
            lines,
            pending: VecDeque::new(),
            stdout: Vec::new(),
            home,
            pids,
            next_id: 0,
        }
    }

    pub fn send_raw(&mut self, line: &str) {
        let stdin = self.stdin.as_mut().unwrap();
        writeln!(stdin, "{line}").unwrap();
        stdin.flush().unwrap();
    }

    fn next_message(&mut self, deadline: Instant) -> Value {
        let wait = deadline.saturating_duration_since(Instant::now());
        let line = self.lines.recv_timeout(wait).expect("a message in time");
        self.stdout.push(line.clone());
        serde_json::from_str(&line).unwrap()
    }

    /// Send a request and wait for its response, keeping notifications.
    pub fn request(&mut self, method: &str, params: Value) -> Value {
        self.next_id += 1;
        let id = self.next_id;
        let request = json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params});
        self.send_raw(&request.to_string());
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let message = self.next_message(deadline);
            if message["id"] == id {
                return message;
            }
            self.pending.push_back(message);
        }
    }

    pub fn initialize(&mut self) {
        let params = json!({"protocolVersion": 1, "clientInfo": {"name": "test", "version": "0"}});
        let reply = self.request("initialize", params);
        assert!(reply.get("result").is_some(), "{reply}");
    }

    /// `run/start` for a run that hangs, unless its id says otherwise.
    pub fn start_run(&mut self, run_id: &str, extra: Value) -> Value {
        let mut params = json!({"runId": run_id, "prompt": "hi", "agentSocket": "agent.sock"});
        params
            .as_object_mut()
            .unwrap()
            .extend(extra.as_object().cloned().unwrap_or_default());
        self.request("run/start", params)
    }

    /// The run's `run/ended` params.
    pub fn ended(&mut self, run_id: &str, within: Duration) -> Value {
        let found = self
            .pending
            .iter()
            .position(|m| m["method"] == "run/ended" && m["params"]["runId"] == run_id);
        if let Some(at) = found {
            return self.pending.remove(at).unwrap()["params"].clone();
        }
        let deadline = Instant::now() + within;
        loop {
            let message = self.next_message(deadline);
            if message["method"] == "run/ended" && message["params"]["runId"] == run_id {
                return message["params"].clone();
            }
            self.pending.push_back(message);
        }
    }

    pub fn close_stdin(&mut self) {
        self.stdin = None;
    }

    /// The exit code and stderr, after every stdout line is read.
    pub fn wait(&mut self) -> (Option<i32>, String) {
        self.close_stdin();
        let mut stderr = String::new();
        self.child
            .stderr
            .take()
            .unwrap()
            .read_to_string(&mut stderr)
            .unwrap();
        let status = self.child.wait().unwrap();
        while let Ok(line) = self.lines.recv_timeout(Duration::from_secs(5)) {
            self.stdout.push(line.clone());
            self.pending.push_back(serde_json::from_str(&line).unwrap());
        }
        (status.code(), stderr)
    }

    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    /// App-server processes started so far.
    pub fn app_servers(&self) -> usize {
        std::fs::read_dir(&self.pids).unwrap().count()
    }

    /// Each started app-server's `MALLOC_ARENA_MAX`.
    pub fn app_server_arenas(&self) -> Vec<String> {
        std::fs::read_dir(&self.pids)
            .unwrap()
            .map(|e| std::fs::read_to_string(e.unwrap().path()).unwrap())
            .collect()
    }

    pub fn app_server_pids(&self) -> Vec<i32> {
        std::fs::read_dir(&self.pids)
            .unwrap()
            .filter_map(|e| e.ok()?.file_name().to_str()?.parse().ok())
            .collect()
    }

    /// Everything under the server's `VIBE_HOME` logs, as text.
    pub fn logs(&self) -> String {
        let dir = self.home.join("logs");
        let Ok(entries) = std::fs::read_dir(dir) else {
            return String::new();
        };
        entries
            .filter_map(|e| std::fs::read_to_string(e.ok()?.path()).ok())
            .collect()
    }
}

impl Drop for RunServer {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = std::fs::remove_dir_all(&self.home);
    }
}

fn fake_unique() -> usize {
    static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
}
