//! Lightweight JSON-RPC replay server for the client-e2e parity suite.

use std::collections::{HashMap, HashSet};
use std::fs::OpenOptions;
use std::io::{BufRead, Read, Write};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, Sender};
use std::sync::Arc;
use std::thread;

use serde_json::{json, Map, Value};

const TURN_ENQUEUE: &str = "session/turn/enqueue";
const TURN_START: &str = "turn/start";
const TURN_QUEUE_REPLACE: &str = "session/turn/queue/replace";
const AGENT_UPDATE: &str = "session/agent/update";
const SHELL_COMMAND: &str = "session/shellCommand";
const SESSION_RESUME: &str = "session/resume";
const SESSION_STOP: &str = "session/stop";
const FIXTURE_ENV: &str = "VIBE_REPLAY_FIXTURE";
const STEP_FIFO_ENV: &str = "VIBE_REPLAY_STEP_FIFO";
const REQUEST_LOG_ENV: &str = "VIBE_REPLAY_REQUEST_LOG";

/// One released batch: the client message id, optional queue id, and its events.
type Batch = (Option<String>, Option<String>, Vec<Value>);

/// Methods answered with another method's canned result (reconnect reuses start).
fn resolve_response<'a>(handshake: &'a Map<String, Value>, method: &str) -> Option<&'a Value> {
    if let Some(result) = handshake.get(method) {
        return Some(result);
    }
    let alias = match method {
        "session/resume" | "session/continue" => "session/start",
        _ => return None,
    };
    handshake.get(alias)
}

fn idempotency_conflict(
    seen: &mut HashMap<String, (String, Value)>,
    method: &str,
    params: Option<&Value>,
) -> Option<String> {
    if !matches!(method, TURN_ENQUEUE | TURN_QUEUE_REPLACE) {
        return None;
    }
    let params = params?;
    let key = params.get("idempotencyKey")?.as_str()?.to_owned();
    let mut input = params.clone();
    if method == TURN_QUEUE_REPLACE {
        input.as_object_mut()?.remove("queueItemId");
    }
    match seen.get(&key) {
        Some((existing_method, existing)) if existing_method != method || existing != &input => {
            Some(key)
        }
        Some(_) => None,
        None => {
            seen.insert(key, (method.to_owned(), input));
            None
        }
    }
}

/// The saved session only answers a resume that names it; a reconnect resumes
/// the client's live session, which `session/start` already holds.
fn resume_response(handshake: &Map<String, Value>, params: Option<&Value>) -> Option<Value> {
    let saved = handshake
        .get("session/resume")
        .and_then(|resume| resume.pointer("/state/session/id"))
        .and_then(Value::as_str);
    let requested = params?.get("sessionId").and_then(Value::as_str);
    match (saved, requested) {
        (Some(saved), Some(requested)) if saved != requested => {
            handshake.get("session/start").cloned()
        }
        _ => handshake.get("session/resume").cloned(),
    }
}

/// Answer an agent switch from the stored runtime: the requested agent becomes
/// the active one, and `adopt_runtime` keeps it for later reads, so a scenario
/// can cycle through every agent like the real server.
fn agent_update_response(handshake: &Map<String, Value>, params: Option<&Value>) -> Option<Value> {
    let name = params?.get("agentName")?.as_str()?;
    let mut runtime = handshake.get("runtime/read")?.get("runtime")?.clone();
    let agent = runtime
        .get("agents")?
        .as_array()?
        .iter()
        .find(|agent| agent.get("name").and_then(Value::as_str) == Some(name))?
        .clone();
    runtime["activeAgent"] = agent;
    Some(json!({ "runtime": runtime }))
}

/// Rewrite the first user message entry's id to the turn's client id.
fn stamp_user_message_id(batch: &mut [Value], client_message_id: &str) {
    for event in batch.iter_mut() {
        if event.get("method").and_then(Value::as_str) != Some("history/entryAdded") {
            continue;
        }
        let Some(entry) = event.pointer_mut("/params/entry") else {
            continue;
        };
        let is_user = entry.get("type").and_then(Value::as_str) == Some("message")
            && entry.get("role").and_then(Value::as_str) == Some("user");
        if is_user {
            entry["id"] = json!(client_message_id);
            return;
        }
    }
}

fn stamp_shell_operation_id(events: &mut [Value], operation_id: &str) {
    for event in events {
        if event.pointer("/params/entry/id").and_then(Value::as_str) == Some("$operationId") {
            event["params"]["entry"]["id"] = json!(operation_id);
        }
    }
}

/// The runtime the emulated server is in right now; `runtime/read` holds it.
fn current_runtime(handshake: &Map<String, Value>) -> Option<&Value> {
    handshake.get("runtime/read")?.get("runtime")
}

/// A mutation's runtime becomes the server's state, so the next read sees it;
/// the connector catalog is a projection of that runtime, so it follows along.
fn adopt_runtime(handshake: &mut Map<String, Value>, catalog: Option<&Value>, result: &Value) {
    let Some(runtime) = result.get("runtime").cloned() else {
        return;
    };
    // Patch the field, never the whole response: `runtime/read` also carries
    // `sessionLog` and `ready`, which the client requires.
    set_field(handshake, "runtime/read", "runtime", runtime);
    let Some(catalog) = catalog.cloned() else {
        return;
    };
    for method in ["connector_catalog/read", "connectors/read"] {
        if handshake.contains_key(method) {
            handshake.insert(method.to_owned(), catalog.clone());
        }
    }
}

/// Methods that report the server's state instead of changing it: they answer
/// with the current runtime, never with the one the fixture was built from.
const READ_LIKE: [&str; 6] = [
    "mcp/read",
    "mcp_catalog/read",
    "mcp/refresh",
    "mcp_catalog/refresh",
    "connector_catalog/refresh",
    "connectors/refresh",
];

/// Serve the current state from a read the scenario did not pin, so a read or a
/// refresh never rewinds the server to the fixture's initial runtime.
fn fill_runtime(handshake: &Map<String, Value>, result: &mut Value) {
    let Some(current) = current_runtime(handshake).cloned() else {
        return;
    };
    if result.get("mcp").is_some() {
        if let Some(mcp) = current.get("mcp").cloned() {
            result["mcp"] = mcp;
        }
    }
    if result.get("runtime").is_some() {
        result["runtime"] = current;
    }
}

/// Overwrite one field of a stored response, leaving its siblings intact.
fn set_field(handshake: &mut Map<String, Value>, method: &str, field: &str, value: Value) {
    if let Some(Value::Object(response)) = handshake.get_mut(method) {
        response.insert(field.to_owned(), value);
    }
}

/// Serialize and enqueue a frame for the single stdout writer.
fn send(writer: &Sender<String>, frame: Value) {
    if let Ok(line) = serde_json::to_string(&frame) {
        let _ = writer.send(line);
    }
}

/// Append a client-to-server JSON-RPC frame when the parity harness requests it.
fn log_request(message: &Value) {
    let Some(path) = std::env::var_os(REQUEST_LOG_ENV) else {
        return;
    };
    let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) else {
        return;
    };
    let Ok(line) = serde_json::to_string(message) else {
        return;
    };
    let _ = writeln!(file, "{line}");
}

/// The queue id the Nth enqueue is answered with; the first keeps the fixture's
/// id so a hand-written handshake stays readable.
fn queue_item_id(base: &str, index: usize) -> String {
    match index {
        0 => base.to_owned(),
        _ => format!("{base}-{}", index + 1),
    }
}

/// Tie a batch's turn to the enqueue that released it, as the server does.
fn stamp_queue_item_id(batch: &mut [Value], queue_item_id: &str) {
    for event in batch.iter_mut() {
        let method = event.get("method").and_then(Value::as_str);
        if !matches!(method, Some("turn/started" | "turn/completed")) {
            continue;
        }
        if let Some(turn) = event.pointer_mut("/params/turn") {
            turn["queueItemId"] = json!(queue_item_id);
        }
    }
}

/// Send one event, numbering it from the shared eventId counter.
fn send_event(writer: &Sender<String>, event_id: &AtomicU64, event: &Value) {
    let method = event.get("method").cloned().unwrap_or(Value::Null);
    let mut params = event
        .get("params")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    // A fixture event with an id is a server -> client request (e.g.
    // `callback/call`), whose params are typed and take no `eventId`.
    if let Some(id) = event.get("id") {
        send(
            writer,
            json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}),
        );
        return;
    }
    let next = event_id.fetch_add(1, Ordering::Relaxed);
    params.insert("eventId".into(), json!(next));
    send(
        writer,
        json!({"jsonrpc": "2.0", "method": method, "params": params}),
    );
}

/// Drain released batches, stamping a monotonic eventId across all of them.
fn run_emitter(
    rx: std::sync::mpsc::Receiver<Batch>,
    writer: Sender<String>,
    event_id: Arc<AtomicU64>,
) {
    let mut step = std::env::var_os(STEP_FIFO_ENV)
        .and_then(|path| OpenOptions::new().read(true).write(true).open(path).ok());
    while let Ok((client_message_id, queue_item_id, mut batch)) = rx.recv() {
        if let Some(id) = client_message_id.as_deref() {
            stamp_user_message_id(&mut batch, id);
        }
        if let Some(id) = queue_item_id.as_deref() {
            stamp_queue_item_id(&mut batch, id);
        }
        for event in batch {
            await_step(&mut step);
            send_event(&writer, &event_id, &event);
        }
    }
}

/// Block until the capture releases the next event; pass straight through if unwired.
fn await_step(step: &mut Option<std::fs::File>) {
    if let Some(file) = step {
        let mut byte = [0u8; 1];
        let _ = file.read_exact(&mut byte);
    }
}

/// The scenario's canned answers: handshake results, the per-enqueue event
/// batches, and the events each request emits while it is answered.
struct Fixture {
    handshake: Map<String, Value>,
    batches: Vec<Vec<Value>>,
    on_request: Map<String, Value>,
    /// Methods the scenario pinned: they answer as declared, state or not.
    declared: HashSet<String>,
    /// The connector catalog projected from each response runtime, by method.
    catalogs: Map<String, Value>,
}

fn load_fixture() -> Fixture {
    let path = std::env::var(FIXTURE_ENV).expect("VIBE_REPLAY_FIXTURE is not set");
    let raw = std::fs::read_to_string(&path).expect("cannot read replay fixture");
    let fixture: Value = serde_json::from_str(&raw).expect("invalid replay fixture");
    let object = |key| {
        fixture
            .get(key)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default()
    };
    let batches = fixture
        .get("events")
        .and_then(Value::as_array)
        .map(|batches| {
            batches
                .iter()
                .map(|b| b.as_array().cloned().unwrap_or_default())
                .collect()
        })
        .unwrap_or_default();
    let declared = fixture
        .get("declared")
        .and_then(Value::as_array)
        .map(|methods| {
            methods
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();
    Fixture {
        handshake: object("handshake"),
        batches,
        on_request: object("onRequest"),
        declared,
        catalogs: object("catalogs"),
    }
}

fn main() {
    let Fixture {
        mut handshake,
        batches,
        on_request,
        declared,
        catalogs,
    } = load_fixture();
    // Shared with the emitter thread so both number their events in one sequence.
    let event_id = Arc::new(AtomicU64::new(1));

    let (writer_tx, writer_rx) = channel::<String>();
    thread::spawn(move || {
        let stdout = std::io::stdout();
        let mut out = stdout.lock();
        while let Ok(line) = writer_rx.recv() {
            if out.write_all(line.as_bytes()).is_err() || out.write_all(b"\n").is_err() {
                break;
            }
            let _ = out.flush();
        }
    });

    let (emitter_tx, emitter_rx) = channel::<Batch>();
    let emitter_writer = writer_tx.clone();
    let emitter_event_id = event_id.clone();
    thread::spawn(move || run_emitter(emitter_rx, emitter_writer, emitter_event_id));

    let mut next_batch = 0usize;
    let mut enqueues = 0usize;
    let mut idempotency = HashMap::new();
    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let Ok(msg) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        log_request(&msg);
        let (Some(id), Some(method)) = (
            msg.get("id").filter(|id| !id.is_null()),
            msg.get("method").and_then(Value::as_str),
        ) else {
            continue;
        };
        if let Some(key) = idempotency_conflict(&mut idempotency, method, msg.get("params")) {
            send(
                &writer_tx,
                json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": {
                        "code": "conflict",
                        "message": format!("Idempotency key was already used with different input: {key}"),
                        "data": {"idempotencyKey": key},
                    },
                }),
            );
            continue;
        }
        // Every accepted prompt gets its own queue id, like the real server; the
        // batch it releases carries that id on its turn. `turn/start` (injected
        // retry) also releases a batch, with the client message id from its params.
        let queued = (method == TURN_ENQUEUE || method == TURN_START).then(|| {
            let base = handshake
                .get(TURN_ENQUEUE)
                .and_then(|result| result.get("queueItemId"))
                .and_then(Value::as_str)
                .unwrap_or_default();
            let id = queue_item_id(base, enqueues);
            enqueues += 1;
            id
        });
        // A replace keeps the item's identity, so echo the requested id back.
        let answered_id = queued.clone().or_else(|| {
            (method == TURN_QUEUE_REPLACE)
                .then(|| msg.pointer("/params/queueItemId").and_then(Value::as_str))
                .flatten()
                .map(str::to_owned)
        });
        let shell_run = method == SHELL_COMMAND
            && msg.pointer("/params/action").and_then(Value::as_str) == Some("run");
        let mut request_events = on_request
            .get(method)
            .and_then(Value::as_array)
            .cloned()
            .filter(|_| method != SHELL_COMMAND || shell_run)
            .unwrap_or_default();
        if let Some(operation_id) = msg.pointer("/params/operationId").and_then(Value::as_str) {
            stamp_shell_operation_id(&mut request_events, operation_id);
        }
        if shell_run {
            for event in &request_events {
                send_event(&writer_tx, &event_id, event);
            }
        }
        let response = match method {
            AGENT_UPDATE => agent_update_response(&handshake, msg.get("params")),
            SESSION_RESUME => resume_response(&handshake, msg.get("params")),
            _ => resolve_response(&handshake, method).cloned(),
        };
        match response {
            Some(mut result) => {
                if READ_LIKE.contains(&method) && !declared.contains(method) {
                    fill_runtime(&handshake, &mut result);
                }
                if let (Some(id), Some(result)) = (&answered_id, result.as_object_mut()) {
                    // `turn/start` response is a `TurnStartResponse` with no
                    // `queueItemId` field; injecting one fails validation.
                    if method != TURN_START {
                        result.insert("queueItemId".into(), json!(id));
                    }
                }
                send(
                    &writer_tx,
                    json!({"jsonrpc": "2.0", "id": id, "result": result}),
                );
                // Only a pinned response carries its own state; a filled one
                // already serves the current runtime and its catalog.
                let catalog = declared
                    .contains(method)
                    .then(|| catalogs.get(method))
                    .flatten();
                adopt_runtime(&mut handshake, catalog, &result);
            }
            None => send(
                &writer_tx,
                json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": {"code": "method_not_found", "message": format!("replay: no response for {method}")},
                }),
            ),
        }
        if method == SESSION_STOP {
            break;
        }
        // Shell streaming events precede its response; other request events follow it.
        if method != SHELL_COMMAND {
            for event in &request_events {
                send_event(&writer_tx, &event_id, event);
            }
        }
        if let Some(queue_item_id) = queued {
            if next_batch < batches.len() {
                let client_message_id = msg
                    .pointer("/params/entries/0/entryId")
                    .or_else(|| msg.pointer("/params/clientUserMessageId"))
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                let batch = batches[next_batch].clone();
                let _ = emitter_tx.send((client_message_id, Some(queue_item_id), batch));
                next_batch += 1;
            }
        }
    }
}
