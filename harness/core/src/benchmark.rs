use std::hint::black_box;
use std::sync::Arc;
use std::time::Duration;
use std::time::Instant;

use serde_json::json;

use crate::core::ContentBlock;
use crate::core::HarnessConfig;
use crate::core::HarnessInput;
use crate::core::Message;
use crate::engine::HarnessEngine;
use crate::engine::SessionHandle;

const DEFAULT_SESSION_COUNT: usize = 100;
const DEFAULT_INSPECTION_COUNT: usize = 10_000;

pub fn run(arguments: impl Iterator<Item = String>) -> Result<(), String> {
    let options = Options::parse(arguments)?;
    let report = benchmark(&options)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?
    );
    Ok(())
}

fn benchmark(options: &Options) -> Result<serde_json::Value, String> {
    let engine = Arc::new(HarnessEngine::new());

    let create_started = Instant::now();
    let handles = (0..options.sessions)
        .map(|index| {
            if options.history_messages == 0 {
                engine.create(config(index))
            } else {
                engine.create_with_history(
                    config(index),
                    initial_history(index, options.history_messages),
                )
            }
        })
        .collect::<Result<Vec<_>, _>>()?;
    let create_elapsed = create_started.elapsed();

    let inspect_started = Instant::now();
    for index in 0..options.inspections {
        black_box(engine.inspect(handles[index % handles.len()])?);
    }
    let inspect_elapsed = inspect_started.elapsed();

    let checkpoint_started = Instant::now();
    let checkpoints = handles
        .iter()
        .copied()
        .map(|handle| engine.checkpoint(handle))
        .collect::<Result<Vec<_>, _>>()?;
    let checkpoint_elapsed = checkpoint_started.elapsed();

    let restore_started = Instant::now();
    let restored_handles = checkpoints
        .iter()
        .enumerate()
        .map(|(index, checkpoint)| {
            let serialized =
                serde_json::to_string(checkpoint).map_err(|error| error.to_string())?;
            let decoded = crate::core::decode_checkpoint(&serialized)?;
            engine.restore(config(index), decoded, 0)
        })
        .collect::<Result<Vec<_>, String>>()?;
    let restore_elapsed = restore_started.elapsed();

    let apply_started = Instant::now();
    apply_concurrently(&engine, &handles, options.concurrency)?;
    let apply_elapsed = apply_started.elapsed();

    let close_started = Instant::now();
    for handle in handles.iter().copied() {
        engine.close(handle)?;
    }
    for handle in restored_handles {
        engine.close(handle)?;
    }
    let close_elapsed = close_started.elapsed();

    Ok(json!({
        "benchmark": "unified_harness_engine",
        "profile": if cfg!(debug_assertions) { "debug" } else { "release" },
        "sessions": options.sessions,
        "concurrency": options.concurrency.min(options.sessions),
        "inspections": options.inspections,
        "history_messages": options.history_messages,
        "timings_ms": {
            "create_total": milliseconds(create_elapsed),
            "create_per_session": milliseconds(create_elapsed) / options.sessions as f64,
            "inspect_total": milliseconds(inspect_elapsed),
            "inspect_per_operation": milliseconds(inspect_elapsed) / options.inspections as f64,
            "checkpoint_total": milliseconds(checkpoint_elapsed),
            "checkpoint_per_session": milliseconds(checkpoint_elapsed) / options.sessions as f64,
            "restore_total": milliseconds(restore_elapsed),
            "restore_per_session": milliseconds(restore_elapsed) / options.sessions as f64,
            "concurrent_apply_total": milliseconds(apply_elapsed),
            "concurrent_apply_per_session": milliseconds(apply_elapsed) / options.sessions as f64,
            "close_total": milliseconds(close_elapsed),
            "close_per_session": milliseconds(close_elapsed) / options.sessions as f64,
        },
        "live_sessions_after_close": engine.live_session_count()?,
    }))
}

fn apply_concurrently(
    engine: &Arc<HarnessEngine>,
    handles: &[SessionHandle],
    concurrency: usize,
) -> Result<(), String> {
    let worker_count = concurrency.min(handles.len());
    let failure = std::sync::Mutex::new(None);
    std::thread::scope(|scope| {
        for worker in 0..worker_count {
            let engine = Arc::clone(engine);
            let failure = &failure;
            scope.spawn(move || {
                for (index, handle) in handles
                    .iter()
                    .copied()
                    .enumerate()
                    .skip(worker)
                    .step_by(worker_count)
                {
                    if let Err(error) = engine.apply(handle, input(index)) {
                        *failure.lock().expect("benchmark failure lock poisoned") = Some(error);
                        break;
                    }
                }
            });
        }
    });
    failure
        .into_inner()
        .map_err(|_| "benchmark failure lock poisoned".to_string())?
        .map_or(Ok(()), Err)
}

fn config(index: usize) -> HarnessConfig {
    serde_json::from_value(json!({
        "task_id": format!("benchmark-{index}"),
        "system_instructions": "",
        "settings": {
            "turn": { "max_iterations": 4 },
            "context": { "compaction": { "mode": "disabled" } },
            "tools": {
                "programmatic": { "max_effects": 16, "max_operations": 64 },
                "large_output": { "mode": "disabled" },
                "subagents": { "mode": "enabled" },
                "background_processes": { "mode": "disabled" },
                "command_environment": { "mode": "unix" }
            }
        },
        "capabilities": {
            "tool_groups": [],
            "skills": [],
            "knowledge_folders": [],
            "agent_types": [],
            "hook_bindings": []
        },
        "plugins": []
    }))
    .expect("benchmark config must be valid")
}

fn initial_history(session_index: usize, message_count: usize) -> Vec<Message> {
    (0..message_count)
        .map(|message_index| {
            Message::user(vec![ContentBlock::text(format!(
                "history-{session_index}-{message_index}"
            ))])
        })
        .collect()
}

fn input(index: usize) -> HarnessInput {
    serde_json::from_value(json!({
        "protocol_version": 1,
        "input_id": 1,
        "determinism": { "time_unix_ms": 1, "random_seed": index + 1 },
        "command": {
            "type": "user_message",
            "turn_id": format!("turn-{index}"),
            "mode": "queue",
            "content": [{ "type": "text", "text": "benchmark" }]
        }
    }))
    .expect("benchmark input must be valid")
}

fn milliseconds(duration: Duration) -> f64 {
    duration.as_secs_f64() * 1_000.0
}

struct Options {
    sessions: usize,
    concurrency: usize,
    inspections: usize,
    history_messages: usize,
}

impl Options {
    fn parse(mut arguments: impl Iterator<Item = String>) -> Result<Self, String> {
        let mut options = Self {
            sessions: DEFAULT_SESSION_COUNT,
            concurrency: std::thread::available_parallelism()
                .map(usize::from)
                .unwrap_or(4),
            inspections: DEFAULT_INSPECTION_COUNT,
            history_messages: 0,
        };
        while let Some(argument) = arguments.next() {
            let value = arguments
                .next()
                .ok_or_else(|| format!("missing value for {argument}"))?;
            let parsed = value
                .parse::<usize>()
                .map_err(|_| format!("invalid positive integer for {argument}: {value}"))?;
            if parsed == 0 {
                return Err(format!("{argument} must be greater than zero"));
            }
            match argument.as_str() {
                "--sessions" => options.sessions = parsed,
                "--concurrency" => options.concurrency = parsed,
                "--inspections" => options.inspections = parsed,
                "--history-messages" => options.history_messages = parsed,
                _ => return Err(format!("unknown argument: {argument}")),
            }
        }
        Ok(options)
    }
}
