//! A synchronous Runtime for Core acceptance scenarios.
//!
//! The driver sends serialized Step Protocol commands to a real Core session.
//! It tracks pending Actions and model-input caches as a production Runtime
//! does. It checks the complete transition after each command.

use std::collections::BTreeMap;

use super::*;
use crate::core::HarnessSettings;
use crate::core::Message;
use uuid::Uuid;

/// One Action directive that Core must return at a given position.
#[derive(Debug)]
pub(super) enum ExpectedDirective {
    Dispatch { action: Value },
    Keep { action_id: String },
    Refresh { action: Value },
}

/// Returns the stable fields of an agent completion Action.
pub(super) fn llm_call(iteration: u64) -> Value {
    json!({
        "type": "llm_call",
        "purpose": "agent",
        "iteration": iteration,
    })
}

/// Returns the stable fields of a compaction completion Action.
pub(super) fn compaction_call(trigger: &str, iteration: u64, attempt: u64) -> Value {
    json!({
        "type": "llm_call",
        "purpose": "compaction",
        "trigger": trigger,
        "attempt": attempt,
        "iteration": iteration,
    })
}

/// Returns the route and arguments of a Runtime builtin tool Action.
pub(super) fn runtime_tool(name: &str, arguments: Value) -> Value {
    json!({
        "type": "runtime_builtin_tool_call",
        "call": {
            "type": "runtime_builtin",
            "name": name,
            "arguments": arguments,
        },
    })
}

/// Returns the stable fields of a private filesystem write Action.
pub(super) fn filesystem_write(path: &str) -> Value {
    json!({
        "type": "filesystem",
        "operation": {
            "type": "write",
            "workspace_path": path,
        },
    })
}

/// Returns a hook Action with its bindings and optional input.
pub(super) fn hook_call(point: &str, binding_ids: &[&str], input: Value) -> Value {
    let mut action = json!({
        "type": "hook_call",
        "hook_binding_ids": binding_ids,
        "hook": point,
    });
    if !input.is_null() {
        action["input"] = input;
    }
    action
}

/// The directives, Observations, and turn state that Core must return.
pub(super) struct ExpectedTransition {
    directives: Vec<ExpectedDirective>,
    observations: Vec<Value>,
    turn: Value,
}

impl ExpectedTransition {
    /// Adds one new Action in protocol order.
    pub(super) fn dispatch(mut self, action: Value) -> Self {
        self.directives.push(ExpectedDirective::Dispatch { action });
        self
    }

    /// Adds `keep` for an Action that the Runtime already owns.
    pub(super) fn keep(mut self, action: &Value) -> Self {
        self.directives.push(ExpectedDirective::Keep {
            action_id: action_id(action).to_string(),
        });
        self
    }

    /// Replaces one pending completion Action without changing its identity.
    pub(super) fn refresh(mut self, action: Value) -> Self {
        self.directives.push(ExpectedDirective::Refresh { action });
        self
    }

    /// Adds one exact Observation in protocol order.
    pub(super) fn observe(mut self, observation: Value) -> Self {
        self.observations.push(observation);
        self
    }

    /// Adds the tool result from the command as an exact Observation.
    pub(super) fn observe_tool_result(mut self, action: &Value, command: &Value) -> Self {
        let turn_id = self.turn["turn_id"]
            .as_str()
            .expect("expected transition has a turn ID");
        self.observations.push(tool_result_committed(
            turn_id,
            action,
            command["result"].clone(),
        ));
        self
    }

    /// Adds the terminal top-level execution Observation for one tool result.
    pub(super) fn observe_tool_execution_finished(mut self, call_id: &str, result: Value) -> Self {
        let turn_id = self.turn["turn_id"]
            .as_str()
            .expect("expected transition has a turn ID");
        self.observations
            .push(tool_execution_finished(turn_id, call_id, result));
        self
    }

    /// Adds the committed Action result followed by its terminal top-level execution.
    pub(super) fn observe_completed_tool_result(self, action: &Value, command: &Value) -> Self {
        self.observe_tool_result(action, command)
            .observe_tool_execution_finished(
                action["call_id"]
                    .as_str()
                    .expect("tool Action has a call ID"),
                command["result"].clone(),
            )
    }
}

/// Starts an expectation for a running turn.
pub(super) fn running(turn_id: &str) -> ExpectedTransition {
    ExpectedTransition {
        directives: Vec::new(),
        observations: Vec::new(),
        turn: json!({"status": "running", "turn_id": turn_id}),
    }
}

/// Starts an expectation for between-turn compaction.
pub(super) fn compacting(trigger: &str) -> ExpectedTransition {
    ExpectedTransition {
        directives: Vec::new(),
        observations: Vec::new(),
        turn: json!({"status": "compacting", "trigger": trigger}),
    }
}

/// Starts an expectation for an idle session.
pub(super) fn idle() -> ExpectedTransition {
    ExpectedTransition {
        directives: Vec::new(),
        observations: Vec::new(),
        turn: json!({"status": "idle"}),
    }
}

/// Starts an expectation for a completed turn with exact output.
pub(super) fn completed(turn_id: &str, output: Vec<Value>) -> ExpectedTransition {
    ExpectedTransition {
        directives: Vec::new(),
        observations: Vec::new(),
        turn: json!({
            "status": "completed",
            "turn_id": turn_id,
            "output": output,
        }),
    }
}

/// Starts an expectation for an interrupted turn.
pub(super) fn interrupted(turn_id: &str, reason: &str) -> ExpectedTransition {
    ExpectedTransition {
        directives: Vec::new(),
        observations: Vec::new(),
        turn: json!({
            "status": "interrupted",
            "turn_id": turn_id,
            "reason": reason,
        }),
    }
}

/// Starts an expectation for a failed turn with its exact protocol error.
pub(super) fn failed(turn_id: &str, error: Value) -> ExpectedTransition {
    ExpectedTransition {
        directives: Vec::new(),
        observations: Vec::new(),
        turn: json!({
            "status": "failed",
            "turn_id": turn_id,
            "error": error,
        }),
    }
}

/// New Actions from a transition that the driver already checked.
pub(super) struct AppliedTransition {
    actions: Vec<Value>,
}

impl AppliedTransition {
    /// Returns the only new Action. Fails unless exactly one Action exists.
    pub(super) fn only_action(self) -> Value {
        let [action]: [Value; 1] = self
            .actions
            .try_into()
            .expect("transition exposes exactly one dispatched or refreshed action");
        action
    }

    /// Returns all new or refreshed Actions in directive order.
    pub(super) fn actions(self) -> Vec<Value> {
        self.actions
    }
}

#[derive(Default)]
struct ModelInputCache {
    messages: Vec<Value>,
    message_revision: Option<u64>,
    message_update: Option<Value>,
    tools: Vec<Value>,
    tool_revision: Option<u64>,
    tool_update: Option<Value>,
}

struct HookBinding {
    point: String,
    order: i64,
}

struct ModelRequest {
    action_id: String,
    messages: Vec<Value>,
    tools: Vec<Value>,
}

/// Drives one real `HarnessSession` as a synchronous Runtime.
pub(super) struct SynchronousRuntime {
    config: HarnessConfig,
    session: HarnessSession,
    next_input_id: u64,
    pending_actions: BTreeMap<String, Value>,
    model_input: ModelInputCache,
    model_requests: Vec<ModelRequest>,
    hook_bindings: BTreeMap<String, HookBinding>,
}

impl SynchronousRuntime {
    /// Creates Core. Indexes the hook bindings that Core can select.
    pub(super) fn new(config: HarnessConfig) -> Self {
        Self::new_with_history(config, Vec::new())
    }

    /// Creates Core with imported model history.
    pub(super) fn new_with_history(config: HarnessConfig, initial_history: Vec<Message>) -> Self {
        let hook_bindings = configured_hook_bindings(&config);
        let session = HarnessSession::create_with_history(config.clone(), initial_history)
            .expect("runtime session creates");
        Self {
            config,
            session,
            next_input_id: 1,
            pending_actions: BTreeMap::new(),
            model_input: ModelInputCache::default(),
            model_requests: Vec::new(),
            hook_bindings,
        }
    }

    pub(super) fn checkpoint_value(&self) -> Value {
        serde_json::to_value(self.session.checkpoint().expect("Core checkpoints"))
            .expect("Core checkpoint serializes")
    }

    pub(super) fn inspection_value(&self) -> Value {
        serde_json::to_value(self.session.inspect()).expect("Core inspection serializes")
    }

    /// Restores Core from a checkpoint. Resets delivery IDs and Runtime caches.
    pub(super) fn restart_from_checkpoint(&mut self) {
        self.restart_from_checkpoint_with_config(self.config.clone());
    }

    /// Restores Core with the Host's current configuration.
    pub(super) fn restart_from_checkpoint_with_config(&mut self, config: HarnessConfig) {
        let checkpoint = self.session.checkpoint().expect("Core checkpoints");
        self.hook_bindings = configured_hook_bindings(&config);
        self.session = HarnessSession::restore(config.clone(), checkpoint, 0)
            .expect("Core restores its checkpoint with current configuration");
        self.config = config;
        self.next_input_id = 1;
        self.model_input = ModelInputCache::default();
        self.model_requests.clear();
        self.assert_pending_actions_match_inspection();
    }

    /// Replaces complete Core settings and retains them for later restoration.
    pub(super) fn reconfigure_settings(
        &mut self,
        settings: HarnessSettings,
        expected_turn: ExpectedTransition,
    ) {
        self.apply(
            json!({
                "type": "reconfigure",
                "changes": [{"type": "settings", "value": settings}],
            }),
            expected_turn,
        );
        self.config.settings = settings;
    }

    /// Drops both disposable model-input caches without changing pending work.
    pub(super) fn lose_model_input_cache(&mut self) {
        self.model_input = ModelInputCache::default();
        self.model_requests.clear();
    }

    /// Queues a user message. Checks the first completion transition.
    pub(super) fn start_turn(&mut self, turn_id: &str, text: &str) -> Value {
        let action = self
            .apply(
                user_message(turn_id, text, "queue"),
                running(turn_id)
                    .dispatch(llm_call(0))
                    .observe(turn_started(turn_id, text)),
            )
            .only_action();
        self.assert_last_model_message_update(
            &action,
            "replace",
            &[model_system(), model_user_text(text)],
        );
        action
    }

    /// Sends a text completion. Checks the terminal Observations.
    pub(super) fn finish_turn_with_text(
        &mut self,
        turn_id: &str,
        completion_action: &Value,
        text: &str,
    ) {
        self.apply(
            text_completion(completion_action, text),
            completed(turn_id, vec![json!({"type": "text", "text": text})])
                .observe(assistant_text_committed(turn_id, completion_action, text))
                .observe(turn_completed(turn_id, text)),
        );
    }

    /// Sends a completion that calls `run_typescript`. Checks its Runtime Actions.
    pub(super) fn complete_with_typescript_program(
        &mut self,
        turn_id: &str,
        completion_action: &Value,
        call_id: &str,
        source: &str,
        expected_actions: impl IntoIterator<Item = Value>,
    ) -> AppliedTransition {
        let expected = expected_actions
            .into_iter()
            .fold(running(turn_id), ExpectedTransition::dispatch)
            .observe(tool_execution_started(turn_id, call_id));
        self.apply(
            run_typescript_completion(action_id(completion_action), call_id, source),
            expected,
        )
    }

    /// Sends direct tool calls. Checks their commit and Runtime Actions.
    pub(super) fn complete_with_tool_calls(
        &mut self,
        turn_id: &str,
        completion_action: &Value,
        calls: &[Value],
        expected_actions: impl IntoIterator<Item = Value>,
    ) -> AppliedTransition {
        let expected = expected_actions
            .into_iter()
            .fold(running(turn_id), ExpectedTransition::dispatch)
            .observe(assistant_tool_calls_committed(
                turn_id,
                completion_action,
                calls,
            ));
        let expected = calls.iter().fold(expected, |expected, call| {
            expected.observe(tool_execution_started(
                turn_id,
                call["id"].as_str().expect("tool call has an ID"),
            ))
        });
        self.apply(
            tool_call_completion(action_id(completion_action), calls.to_vec()),
            expected,
        )
    }

    /// Sends steering. Requires Core to keep each supplied Action.
    pub(super) fn receive_steering(
        &mut self,
        turn_id: &str,
        text: &str,
        pending_actions: &[&Value],
    ) {
        let expected = pending_actions
            .iter()
            .fold(running(turn_id), |expected, action| expected.keep(action))
            .observe(turn_steering_received(turn_id, text));
        self.apply(user_message(turn_id, text, "steer"), expected);
    }

    /// Sends a notification. Requires Core to keep each supplied Action.
    pub(super) fn receive_notification(
        &mut self,
        turn_id: &str,
        command: &Value,
        pending_actions: &[&Value],
    ) {
        let expected = pending_actions
            .iter()
            .fold(running(turn_id), |expected, action| expected.keep(action))
            .observe(notification_received(
                turn_id,
                observed_notification(command),
            ));
        self.apply(command.clone(), expected);
    }

    /// Sends one command. Checks the complete transition and Runtime state.
    pub(super) fn apply(
        &mut self,
        command: Value,
        expected: ExpectedTransition,
    ) -> AppliedTransition {
        self.assert_result_correlation(&command);
        let input_id = self.next_input_id;
        let result = accepted_value(self.session.apply(input(input_id, command)));
        self.next_input_id += 1;

        let transition = result["transition"].clone();
        assert_eq!(transition["protocol_version"], 1);
        assert_eq!(transition["input_id"], input_id);
        assert_eq!(
            transition["observations"],
            Value::Array(expected.observations),
            "transition observations differ"
        );
        assert_eq!(transition["turn"], expected.turn, "turn state differs");

        let actions = self.assert_directives(&transition["next"], &expected.directives);
        self.reconcile_pending_actions(&transition["next"]);
        self.assert_turn_action_invariants(&transition);
        self.assert_pending_actions_match_inspection();
        AppliedTransition { actions }
    }

    /// Sends one invalid command. Checks that rejection does not mutate Core or consume the input ID.
    pub(super) fn reject(&mut self, command: Value, expected_rejection: Value) {
        let before_checkpoint = self.checkpoint_value();
        let before_inspection = self.inspection_value();
        let result = serde_json::to_value(self.session.apply(input(self.next_input_id, command)))
            .expect("Core rejection serializes");

        assert_eq!(
            result,
            json!({"type": "rejected", "rejection": expected_rejection})
        );
        assert_eq!(self.checkpoint_value(), before_checkpoint);
        assert_eq!(self.inspection_value(), before_inspection);
        self.assert_pending_actions_match_inspection();
    }

    /// Checks the complete message update used for the last model request.
    pub(super) fn assert_last_model_message_update(
        &self,
        action: &Value,
        expected_update_type: &str,
        expected_messages: &[Value],
    ) {
        let request = self
            .model_requests
            .last()
            .expect("Runtime recorded a model request");
        assert_eq!(request.action_id, action_id(action));
        let update = &action["model_input"]["messages"];
        assert_eq!(
            update["type"], expected_update_type,
            "model-message update type differs"
        );
        assert_json_subset(
            &Value::Array(expected_messages.to_vec()),
            &update["messages"],
        );
        assert!(
            request.messages.len() >= expected_messages.len(),
            "Runtime model request has too few messages: {:?}",
            request.messages
        );
        let offset = request.messages.len() - expected_messages.len();
        assert_json_subset(
            &Value::Array(expected_messages.to_vec()),
            &Value::Array(request.messages[offset..].to_vec()),
        );
        assert!(
            !request.tools.is_empty(),
            "Runtime model request has no tool catalog"
        );
    }

    /// Rejects a result for an unknown Action or the wrong Action type.
    fn assert_result_correlation(&self, command: &Value) {
        let Some(command_type) = command["type"].as_str() else {
            panic!("Runtime command has no type: {command}");
        };
        let expected_action_type = match command_type {
            "completion_model_input_resync_requested"
            | "completion_succeeded"
            | "completion_failed" => Some("llm_call"),
            "hook_completed" | "hook_failed" => Some("hook_call"),
            "tool_succeeded" | "tool_failed" => Some("tool"),
            "filesystem_succeeded" | "filesystem_failed" => Some("filesystem"),
            "fail_turn" => Some("pending"),
            _ => None,
        };
        let Some(expected_action_type) = expected_action_type else {
            return;
        };
        let command_action_id = command["action_id"]
            .as_str()
            .expect("correlated Runtime command has an action ID");
        let action = self
            .pending_actions
            .get(command_action_id)
            .unwrap_or_else(|| {
                panic!("Runtime tried to resolve unknown action {command_action_id:?}")
            });
        match expected_action_type {
            "tool" => {
                assert!(
                    matches!(
                        action["type"].as_str(),
                        Some("runtime_builtin_tool_call" | "provided_tool_call")
                    ),
                    "Runtime tool result targeted non-tool action {action}"
                );
                assert_eq!(
                    command["call_id"], action["call_id"],
                    "Runtime tool result targeted the wrong call"
                );
            }
            "pending" => {}
            expected => assert_eq!(
                action["type"], expected,
                "Runtime result targeted the wrong action type"
            ),
        }
    }

    /// Checks the directive count and order. Returns only new Actions.
    fn assert_directives(&self, next: &Value, expected: &[ExpectedDirective]) -> Vec<Value> {
        if expected.is_empty() {
            assert_eq!(next, &json!({"type": "none"}), "expected no next action");
            return Vec::new();
        }
        assert_eq!(next["type"], "actions");
        let directives = next["directives"]
            .as_array()
            .expect("action continuation has directives");
        assert_eq!(
            directives.len(),
            expected.len(),
            "transition directive count differs"
        );
        let mut actions = Vec::new();
        for (directive, expected) in directives.iter().zip(expected) {
            match expected {
                ExpectedDirective::Dispatch { action } => {
                    assert_eq!(directive["type"], "dispatch");
                    assert_json_subset(action, &directive["action"]);
                    actions.push(directive["action"].clone());
                }
                ExpectedDirective::Keep { action_id } => {
                    assert_canonical_action_id(action_id);
                    assert_eq!(directive, &json!({"type": "keep", "action_id": action_id}));
                }
                ExpectedDirective::Refresh { action } => {
                    assert_eq!(directive["type"], "refresh");
                    assert_json_subset(action, &directive["action"]);
                    actions.push(directive["action"].clone());
                }
            }
        }
        actions
    }

    /// Rebuilds Runtime Action ownership from `keep`, `dispatch`, and `refresh`.
    fn reconcile_pending_actions(&mut self, next: &Value) {
        if next["type"] == "none" {
            self.pending_actions.clear();
            return;
        }
        let previous = std::mem::take(&mut self.pending_actions);
        let mut next_actions = BTreeMap::new();
        for directive in next["directives"]
            .as_array()
            .expect("action continuation has directives")
        {
            match directive["type"].as_str() {
                Some("keep") => {
                    let id = directive["action_id"]
                        .as_str()
                        .expect("keep directive has an action ID");
                    let action = previous
                        .get(id)
                        .unwrap_or_else(|| panic!("Core kept unknown Runtime action {id:?}"));
                    assert!(
                        next_actions
                            .insert(id.to_string(), action.clone())
                            .is_none(),
                        "Core emitted duplicate directive for action {id:?}"
                    );
                }
                Some("dispatch") => {
                    let action = &directive["action"];
                    let id = action_id(action);
                    assert!(
                        !previous.contains_key(id),
                        "Core redispatched pending Runtime action {id:?}"
                    );
                    if action["type"] == "llm_call" {
                        self.apply_model_input_update(action, false);
                        self.record_model_request(action);
                    } else if action["type"] == "hook_call" {
                        self.validate_hook_action(action);
                    }
                    assert!(
                        next_actions
                            .insert(id.to_string(), action.clone())
                            .is_none(),
                        "Core dispatched duplicate Runtime action {id:?}"
                    );
                }
                Some("refresh") => {
                    let action = &directive["action"];
                    let id = action_id(action);
                    let old = previous
                        .get(id)
                        .unwrap_or_else(|| panic!("Core refreshed unknown Runtime action {id:?}"));
                    assert_eq!(old["type"], "llm_call");
                    assert_eq!(action["type"], "llm_call");
                    self.apply_model_input_update(action, true);
                    self.record_model_request(action);
                    assert!(
                        next_actions
                            .insert(id.to_string(), action.clone())
                            .is_none(),
                        "Core emitted duplicate directive for action {id:?}"
                    );
                }
                other => panic!("unsupported Core directive {other:?}"),
            }
        }
        self.pending_actions = next_actions;
    }

    /// Applies message and tool updates with production revision rules.
    fn apply_model_input_update(&mut self, action: &Value, allow_replace_same_revision: bool) {
        let messages = &action["model_input"]["messages"];
        let revision = required_revision(messages);
        match messages["type"].as_str() {
            Some("replace") => {
                if self.model_input.message_revision == Some(revision)
                    && self.model_input.message_update.as_ref() != Some(messages)
                    && !allow_replace_same_revision
                {
                    panic!("Core reused model-message revision {revision} with different content");
                }
                self.model_input.messages = messages["messages"]
                    .as_array()
                    .expect("message replacement contains messages")
                    .clone();
                self.model_input.message_revision = Some(revision);
            }
            Some("append") => {
                if self.model_input.message_revision == Some(revision) {
                    assert_eq!(
                        self.model_input.message_update.as_ref(),
                        Some(messages),
                        "Core reused model-message revision {revision} with a different append"
                    );
                } else {
                    assert_eq!(
                        self.model_input.message_revision,
                        messages["base_revision"].as_u64(),
                        "Core appended model messages from the wrong Runtime revision"
                    );
                    self.model_input.messages.extend(
                        messages["messages"]
                            .as_array()
                            .expect("message append contains messages")
                            .iter()
                            .cloned(),
                    );
                    self.model_input.message_revision = Some(revision);
                }
            }
            other => panic!("unsupported model-message update {other:?}"),
        }
        self.model_input.message_update = Some(messages.clone());

        let tools = &action["model_input"]["tool_catalog"];
        let tool_revision = required_revision(tools);
        match tools["type"].as_str() {
            Some("keep") => assert_eq!(
                self.model_input.tool_revision,
                Some(tool_revision),
                "Core kept the wrong Runtime tool-catalog revision"
            ),
            Some("replace") => {
                if self.model_input.tool_revision == Some(tool_revision)
                    && self.model_input.tool_update.as_ref() != Some(tools)
                    && !allow_replace_same_revision
                {
                    panic!(
                        "Core reused tool-catalog revision {tool_revision} with different content"
                    );
                }
                self.model_input.tools = tools["tools"]
                    .as_array()
                    .expect("tool-catalog replacement contains tools")
                    .clone();
                self.model_input.tool_revision = Some(tool_revision);
                self.model_input.tool_update = Some(tools.clone());
            }
            other => panic!("unsupported model tool-catalog update {other:?}"),
        }
    }

    /// Records the provider request after both caches are current.
    fn record_model_request(&mut self, action: &Value) {
        self.model_requests.push(ModelRequest {
            action_id: action_id(action).to_string(),
            messages: self.model_input.messages.clone(),
            tools: self.model_input.tools.clone(),
        });
    }

    /// Requires active work to own Actions and inactive states to own none.
    fn assert_turn_action_invariants(&self, transition: &Value) {
        match transition["turn"]["status"].as_str() {
            Some("running") => {
                assert_eq!(transition["next"]["type"], "actions");
                assert!(!self.pending_actions.is_empty());
                let turn_id = transition["turn"]["turn_id"]
                    .as_str()
                    .expect("running turn has an ID");
                for action in self.pending_actions.values() {
                    assert_eq!(
                        action["turn_id"], turn_id,
                        "pending Runtime action belongs to another turn"
                    );
                }
            }
            Some("compacting") => {
                assert_eq!(transition["next"]["type"], "actions");
                assert!(!self.pending_actions.is_empty());
                for action in self.pending_actions.values() {
                    assert_eq!(action["turn_id"], Value::Null);
                }
            }
            Some("idle" | "completed" | "interrupted" | "failed") => {
                assert_eq!(transition["next"], json!({"type": "none"}));
                assert!(self.pending_actions.is_empty());
            }
            other => panic!("acceptance scenario returned unexpected turn state {other:?}"),
        }
    }

    /// Compares Runtime Action ownership with Core inspection.
    fn assert_pending_actions_match_inspection(&self) {
        let inspection = serde_json::to_value(self.session.inspect())
            .expect("Core inspection serializes for Runtime comparison");
        let actual = inspection["pending_actions"]
            .as_array()
            .expect("Core inspection contains pending actions")
            .iter()
            .map(|action| {
                (
                    canonical_action_id(
                        action["action_id"]
                            .as_str()
                            .expect("inspected action has an ID"),
                    )
                    .to_string(),
                    action.clone(),
                )
            })
            .collect::<BTreeMap<_, _>>();
        let expected = self
            .pending_actions
            .iter()
            .map(|(id, action)| (id.clone(), pending_action_inspection(action)))
            .collect::<BTreeMap<_, _>>();
        assert_eq!(
            actual, expected,
            "Runtime pending-action ledger differs from Core inspection"
        );
    }

    /// Checks each hook binding and its point, uniqueness, and order.
    fn validate_hook_action(&self, action: &Value) {
        let point = action["hook"]
            .as_str()
            .expect("Runtime hook action has a hook point");
        let binding_ids = action["hook_binding_ids"]
            .as_array()
            .expect("Runtime hook action has binding IDs");
        assert!(
            !binding_ids.is_empty(),
            "Core selected no Runtime hook binding"
        );
        let mut previous_order = None;
        let mut seen = std::collections::BTreeSet::new();
        for id in binding_ids {
            let id = id.as_str().expect("Runtime hook binding ID is a string");
            assert!(
                seen.insert(id),
                "Core selected duplicate Runtime hook binding {id:?}"
            );
            let binding = self
                .hook_bindings
                .get(id)
                .unwrap_or_else(|| panic!("Core selected unknown Runtime hook binding {id:?}"));
            assert_eq!(
                binding.point, point,
                "Core selected Runtime hook binding {id:?} for the wrong point"
            );
            if let Some(previous_order) = previous_order {
                assert!(
                    binding.order > previous_order,
                    "Core selected Runtime hook bindings out of order"
                );
            }
            previous_order = Some(binding.order);
        }
    }
}

/// Returns the required revision from a model-input update.
fn required_revision(update: &Value) -> u64 {
    update["revision"]
        .as_u64()
        .expect("model-input update has a revision")
}

/// Matches stable JSON fields. Keeps array length and order exact.
fn assert_json_subset(expected: &Value, actual: &Value) {
    match (expected, actual) {
        (Value::Object(expected), Value::Object(actual)) => {
            for (key, expected) in expected {
                let actual = actual.get(key).unwrap_or_else(|| {
                    panic!("action is missing expected field {key:?}: {actual:?}")
                });
                assert_json_subset(expected, actual);
            }
        }
        (Value::Array(expected), Value::Array(actual)) => {
            assert_eq!(
                actual.len(),
                expected.len(),
                "action array length differs: expected {expected:?}, got {actual:?}"
            );
            for (expected, actual) in expected.iter().zip(actual) {
                assert_json_subset(expected, actual);
            }
        }
        _ => assert_eq!(actual, expected, "action field differs"),
    }
}

/// Returns the Core Action ID for later Runtime commands.
pub(super) fn action_id(action: &Value) -> &str {
    canonical_action_id(
        action["action_id"]
            .as_str()
            .expect("Runtime action has an action ID"),
    )
}

fn canonical_action_id(action_id: &str) -> &str {
    assert_canonical_action_id(action_id);
    action_id
}

fn assert_canonical_action_id(action_id: &str) {
    let uuid = Uuid::parse_str(action_id)
        .unwrap_or_else(|error| panic!("Runtime action ID {action_id:?} is not a UUID: {error}"));
    assert_eq!(
        uuid.to_string(),
        action_id,
        "Runtime action ID is not a canonical lowercase UUID"
    );
}

/// Converts a Runtime Action to the pending-Action inspection form.
fn pending_action_inspection(action: &Value) -> Value {
    match action["type"].as_str() {
        Some("llm_call") => json!({
            "type": "completion",
            "purpose": action["purpose"],
            "action_id": action["action_id"],
        }),
        Some("runtime_builtin_tool_call") => json!({
            "type": "runtime_builtin_tool_call",
            "action_id": action["action_id"],
            "call_id": action["call_id"],
            "name": action["call"]["name"],
        }),
        Some("provided_tool_call") => json!({
            "type": "provided_tool_call",
            "action_id": action["action_id"],
            "call_id": action["call_id"],
            "group_name": action["call"]["group_name"],
            "tool_name": action["call"]["tool_name"],
        }),
        Some("hook_call") => json!({
            "type": "hook_call",
            "action_id": action["action_id"],
            "hook": action["hook"],
            "hook_binding_ids": action["hook_binding_ids"],
        }),
        Some("filesystem") => json!({
            "type": "filesystem",
            "action_id": action["action_id"],
            "operation": {
                "type": action["operation"]["type"],
                "workspace_path": action["operation"]["workspace_path"],
            },
        }),
        other => panic!("unsupported pending Runtime action {other:?}"),
    }
}

/// Indexes root and plugin hook bindings for hook Action checks.
fn configured_hook_bindings(config: &HarnessConfig) -> BTreeMap<String, HookBinding> {
    let config = serde_json::to_value(config).expect("Runtime config serializes");
    let roots = std::iter::once(&config["capabilities"]).chain(
        config["plugins"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|plugin| &plugin["capabilities"]),
    );
    roots
        .flat_map(|capabilities| {
            capabilities["hook_bindings"]
                .as_array()
                .into_iter()
                .flatten()
        })
        .map(|binding| {
            (
                binding["id"]
                    .as_str()
                    .expect("configured hook binding has an ID")
                    .to_string(),
                HookBinding {
                    point: binding["point"]
                        .as_str()
                        .expect("configured hook binding has a point")
                        .to_string(),
                    order: binding["order"]
                        .as_i64()
                        .expect("configured hook binding has an order"),
                },
            )
        })
        .collect()
}
