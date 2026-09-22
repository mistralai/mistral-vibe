use std::collections::HashMap;
use std::sync::Arc;
#[cfg(any(feature = "node-binding", feature = "python-binding"))]
use std::sync::LazyLock;
use std::sync::Mutex;
use std::sync::RwLock;
use std::sync::atomic::AtomicU64;
use std::sync::atomic::Ordering;

#[cfg(test)]
use crate::core::BackgroundProcessMode;
use crate::core::Checkpoint;
#[cfg(test)]
use crate::core::CommandEnvironment;
#[cfg(test)]
use crate::core::CompactionPolicy;
use crate::core::HarnessApplyResult;
use crate::core::HarnessConfig;
use crate::core::HarnessInput;
use crate::core::HarnessSession;
use crate::core::Inspection;
#[cfg(test)]
use crate::core::ProgrammaticToolSettings;
#[cfg(test)]
use crate::core::SubagentMode;
#[cfg(test)]
use crate::core::config::ContextSettings;
#[cfg(test)]
use crate::core::config::HarnessSettings;
#[cfg(test)]
use crate::core::config::ToolSettings;
#[cfg(test)]
use crate::core::config::TurnSettings;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) struct SessionHandle(u64);

struct SessionSlot {
    session: Mutex<HarnessSession>,
}

pub(crate) struct HarnessEngine {
    next_handle: AtomicU64,
    sessions: RwLock<HashMap<SessionHandle, Arc<SessionSlot>>>,
}

#[cfg(any(feature = "node-binding", feature = "python-binding"))]
pub(crate) static SHARED_ENGINE: LazyLock<Arc<HarnessEngine>> =
    LazyLock::new(|| Arc::new(HarnessEngine::new()));

impl HarnessEngine {
    pub(crate) fn new() -> Self {
        Self {
            next_handle: AtomicU64::new(1),
            sessions: RwLock::new(HashMap::new()),
        }
    }

    #[cfg(any(test, feature = "benchmark"))]
    pub(crate) fn create(&self, config: HarnessConfig) -> Result<SessionHandle, String> {
        self.create_with_history(config, Vec::new())
    }

    pub(crate) fn create_with_history(
        &self,
        config: HarnessConfig,
        initial_history: Vec<crate::core::Message>,
    ) -> Result<SessionHandle, String> {
        let session = HarnessSession::create_with_history(config, initial_history)?;
        self.insert(session)
    }

    pub(crate) fn restore(
        &self,
        config: HarnessConfig,
        checkpoint: Checkpoint,
        resume_input_id: u64,
    ) -> Result<SessionHandle, String> {
        let session = HarnessSession::restore(config, checkpoint, resume_input_id)?;
        self.insert(session)
    }

    pub(crate) fn apply(
        &self,
        handle: SessionHandle,
        input: HarnessInput,
    ) -> Result<HarnessApplyResult, String> {
        self.with_session(handle, |session| session.apply(input))
    }

    pub(crate) fn inspect(&self, handle: SessionHandle) -> Result<Inspection, String> {
        self.with_session(handle, |session| session.inspect())
    }

    pub(crate) fn checkpoint(&self, handle: SessionHandle) -> Result<Checkpoint, String> {
        self.with_session(handle, |session| session.checkpoint())?
    }

    pub(crate) fn close(&self, handle: SessionHandle) -> Result<(), String> {
        self.sessions
            .write()
            .map_err(|_| "Harness Engine session registry is poisoned".to_string())?
            .remove(&handle)
            .ok_or_else(|| unknown_session(handle))?;
        Ok(())
    }

    #[cfg(any(feature = "node-binding", feature = "benchmark", test))]
    pub(crate) fn live_session_count(&self) -> Result<usize, String> {
        self.sessions
            .read()
            .map(|sessions| sessions.len())
            .map_err(|_| "Harness Engine session registry is poisoned".to_string())
    }

    fn insert(&self, session: HarnessSession) -> Result<SessionHandle, String> {
        let raw_handle = self
            .next_handle
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, checked_add_one)
            .map_err(|_| "Harness Engine session handle space is exhausted".to_string())?;
        let handle = SessionHandle(raw_handle);
        let replaced = self
            .sessions
            .write()
            .map_err(|_| "Harness Engine session registry is poisoned".to_string())?
            .insert(
                handle,
                Arc::new(SessionSlot {
                    session: Mutex::new(session),
                }),
            );
        if replaced.is_some() {
            return Err("Harness Engine allocated a duplicate session handle".to_string());
        }
        Ok(handle)
    }

    fn with_session<T>(
        &self,
        handle: SessionHandle,
        operation: impl FnOnce(&mut HarnessSession) -> T,
    ) -> Result<T, String> {
        let slot = self
            .sessions
            .read()
            .map_err(|_| "Harness Engine session registry is poisoned".to_string())?
            .get(&handle)
            .cloned()
            .ok_or_else(|| unknown_session(handle))?;
        let mut session = slot
            .session
            .lock()
            .map_err(|_| "Harness Engine session lock is poisoned".to_string())?;
        Ok(operation(&mut session))
    }
}

fn checked_add_one(value: u64) -> Option<u64> {
    value.checked_add(1)
}

impl Default for HarnessEngine {
    fn default() -> Self {
        Self::new()
    }
}

fn unknown_session(handle: SessionHandle) -> String {
    format!("unknown Harness Engine session handle {}", handle.0)
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::sync::Barrier;
    use std::sync::atomic::AtomicUsize;
    use std::time::Duration;

    use serde_json::Value;
    use serde_json::json;

    use super::*;
    use crate::core::ImageDeliverySettings;

    fn config(task_id: &str) -> HarnessConfig {
        HarnessConfig {
            task_id: task_id.to_string(),
            system_instructions: "system".to_string(),
            settings: HarnessSettings {
                turn: TurnSettings {
                    max_iterations: Some(8),
                },
                context: ContextSettings {
                    compaction: CompactionPolicy::Disabled,
                    image_delivery: ImageDeliverySettings::default(),
                },
                tools: ToolSettings {
                    programmatic: ProgrammaticToolSettings {
                        max_effects: 128,
                        max_operations: 1024,
                    },
                    large_output: crate::core::LargeOutputPolicy::Disabled,
                    subagents: SubagentMode::Enabled,
                    background_processes: BackgroundProcessMode::Disabled,
                    command_environment: CommandEnvironment::Unix,
                },
            },
            capabilities: Default::default(),
            skill_catalog_fingerprint: None,
            plugins: Vec::new(),
        }
    }

    fn input(turn_id: &str) -> HarnessInput {
        serde_json::from_value(json!({
            "protocol_version": 1,
            "input_id": 1,
            "determinism": { "time_unix_ms": 1, "random_seed": 1 },
            "command": {
                "type": "user_message",
                "turn_id": turn_id,
                "mode": "queue",
                "content": [{ "type": "text", "text": "hello" }]
            }
        }))
        .unwrap()
    }

    fn inspection_value(engine: &HarnessEngine, handle: SessionHandle) -> Value {
        serde_json::to_value(engine.inspect(handle).unwrap()).unwrap()
    }

    #[test]
    fn manages_independent_sessions() {
        let engine = HarnessEngine::new();
        let first = engine.create(config("first")).unwrap();
        let second = engine.create(config("second")).unwrap();

        engine.apply(first, input("turn-first")).unwrap();

        assert_eq!(inspection_value(&engine, first)["last_input_id"], 1);
        assert_eq!(inspection_value(&engine, second)["last_input_id"], 0);
        assert_eq!(engine.live_session_count().unwrap(), 2);
    }

    #[test]
    fn restores_a_checkpoint_into_a_new_handle() {
        let engine = HarnessEngine::new();
        let original = engine.create(config("original")).unwrap();
        engine.apply(original, input("turn-original")).unwrap();
        let checkpoint = engine.checkpoint(original).unwrap();

        let restored = engine.restore(config("original"), checkpoint, 0).unwrap();

        assert_ne!(restored, original);
        assert_eq!(inspection_value(&engine, restored)["last_input_id"], 0);
    }

    #[test]
    fn restores_a_checkpoint_at_the_input_cursor_the_caller_recorded() {
        let engine = HarnessEngine::new();
        let original = engine.create(config("original")).unwrap();
        engine.apply(original, input("turn-original")).unwrap();
        let checkpoint = engine.checkpoint(original).unwrap();

        let restored = engine.restore(config("original"), checkpoint, 1).unwrap();

        assert_eq!(inspection_value(&engine, restored)["last_input_id"], 1);
    }

    #[test]
    fn close_rejects_later_operations() {
        let engine = HarnessEngine::new();
        let handle = engine.create(config("closed")).unwrap();

        engine.close(handle).unwrap();

        assert!(
            engine
                .inspect(handle)
                .unwrap_err()
                .contains("unknown Harness Engine")
        );
        assert!(
            engine
                .close(handle)
                .unwrap_err()
                .contains("unknown Harness Engine")
        );
        assert_eq!(engine.live_session_count().unwrap(), 0);
    }

    #[test]
    fn different_sessions_can_be_advanced_from_different_threads() {
        let engine = Arc::new(HarnessEngine::new());
        let handles = (0..32)
            .map(|index| engine.create(config(&format!("session-{index}"))).unwrap())
            .collect::<Vec<_>>();

        std::thread::scope(|scope| {
            for (index, handle) in handles.iter().copied().enumerate() {
                let engine = Arc::clone(&engine);
                scope.spawn(move || {
                    engine
                        .apply(handle, input(&format!("turn-{index}")))
                        .unwrap();
                });
            }
        });

        for handle in handles {
            assert_eq!(inspection_value(&engine, handle)["last_input_id"], 1);
        }
    }

    #[test]
    fn operations_for_one_session_are_serialized() {
        let engine = Arc::new(HarnessEngine::new());
        let handle = engine.create(config("serialized")).unwrap();
        let active = Arc::new(AtomicUsize::new(0));
        let maximum = Arc::new(AtomicUsize::new(0));

        std::thread::scope(|scope| {
            for _ in 0..8 {
                let engine = Arc::clone(&engine);
                let active = Arc::clone(&active);
                let maximum = Arc::clone(&maximum);
                scope.spawn(move || {
                    engine
                        .with_session(handle, |_| {
                            let count = active.fetch_add(1, Ordering::SeqCst) + 1;
                            maximum.fetch_max(count, Ordering::SeqCst);
                            std::thread::sleep(Duration::from_millis(2));
                            active.fetch_sub(1, Ordering::SeqCst);
                        })
                        .unwrap();
                });
            }
        });

        assert_eq!(maximum.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn operations_for_different_sessions_overlap() {
        let engine = Arc::new(HarnessEngine::new());
        let handles = (0..8)
            .map(|index| engine.create(config(&format!("parallel-{index}"))).unwrap())
            .collect::<Vec<_>>();
        let active = Arc::new(AtomicUsize::new(0));
        let maximum = Arc::new(AtomicUsize::new(0));

        std::thread::scope(|scope| {
            for handle in handles {
                let engine = Arc::clone(&engine);
                let active = Arc::clone(&active);
                let maximum = Arc::clone(&maximum);
                scope.spawn(move || {
                    engine
                        .with_session(handle, |_| {
                            let count = active.fetch_add(1, Ordering::SeqCst) + 1;
                            maximum.fetch_max(count, Ordering::SeqCst);
                            std::thread::sleep(Duration::from_millis(10));
                            active.fetch_sub(1, Ordering::SeqCst);
                        })
                        .unwrap();
                });
            }
        });

        assert!(maximum.load(Ordering::SeqCst) > 1);
    }

    #[test]
    fn close_allows_an_in_flight_operation_to_finish() {
        let engine = Arc::new(HarnessEngine::new());
        let handle = engine.create(config("close-race")).unwrap();
        let entered = Arc::new(Barrier::new(2));
        let release = Arc::new(Barrier::new(2));
        let operation = {
            let engine = Arc::clone(&engine);
            let entered = Arc::clone(&entered);
            let release = Arc::clone(&release);
            std::thread::spawn(move || {
                engine
                    .with_session(handle, |_| {
                        entered.wait();
                        release.wait();
                    })
                    .unwrap();
            })
        };

        entered.wait();
        engine.close(handle).unwrap();
        assert!(engine.inspect(handle).is_err());
        release.wait();
        operation.join().unwrap();
        assert_eq!(engine.live_session_count().unwrap(), 0);
    }
}
