//! Instructions held by key: each upsert and removal appends a note, and
//! compaction keeps exactly one note for each live key, right after the
//! system prompt.

use super::*;

const PREAMBLE: &str = r#"You are continuing a trajectory after a context compaction.

Here are some of the most recent previous user messages, preserved verbatim where possible. Treat them as prior context, not as new requests.

<previous_user_messages>"#;

fn upsert(key: &str, content: &str) -> Value {
    json!({"type": "instructions_upsert", "key": key, "content": content})
}

fn delete(key: &str) -> Value {
    json!({"type": "instructions_delete", "key": key})
}

fn context_message(text: &str) -> Value {
    json!({"type": "context_message", "content": [{"type": "text", "text": text}]})
}

/// The note that delivers `content` for `key`, as the model reads it.
fn note(key: &str, content: &str) -> Value {
    model_context_text(&format!(
        "<Instructions key=\"{key}\">\n{content}\n</Instructions>"
    ))
}

/// The note that delivers `content` for a live `key` and says its previous
/// text no longer applies.
fn overwrite_note(key: &str, content: &str) -> Value {
    model_context_text(&format!(
        "Instructions `{key}` have been overwritten: the previous text no longer applies.\n<Instructions key=\"{key}\">\n{content}\n</Instructions>"
    ))
}

/// The note that says the instructions for `key` no longer apply.
fn deletion_note(key: &str) -> Value {
    model_context_text(&format!("Instructions `{key}` no longer apply."))
}

fn run_turn(runtime: &mut SynchronousRuntime, turn_id: &str, request: &str, answer: &str) {
    let action = runtime
        .apply(
            user_message(turn_id, request, "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, request)),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &action, answer);
}

fn compact_manually(runtime: &mut SynchronousRuntime, summary: &str) {
    let action = runtime
        .apply(
            compact(""),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();
    let mut compacted = context_compacted("", &action, summary);
    compacted["turn_id"] = Value::Null;
    compacted["trigger"] = json!("manual");
    runtime.apply(
        summary_completion(&action, summary),
        idle().observe(compacted),
    );
}

/// The system prompt, the live instructions, the preamble, the carried user
/// messages and the summary.
fn rebuilt_head(instructions: Vec<Value>, carried: &[&str], summary: &str) -> Vec<Value> {
    let mut messages = vec![model_system()];
    messages.extend(instructions);
    messages.push(model_user_text(PREAMBLE));
    messages.extend(carried.iter().map(|text| model_user_text(text)));
    messages.push(model_user_text(&format!(
        "</previous_user_messages>\n\nHere is a summary of what has happened so far:\n\n<compaction_summary>\n{summary}\n</compaction_summary>"
    )));
    messages
}

/// Starts a Turn and checks the model message update it sends: `history`
/// then the Turn's user message. Returns the Turn's completion Action.
fn assert_next_turn_input(
    runtime: &mut SynchronousRuntime,
    turn_id: &str,
    update: &str,
    history: Vec<Value>,
) -> Value {
    let action = runtime
        .apply(
            user_message(turn_id, "what next?", "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "what next?")),
        )
        .only_action();
    let mut expected = history;
    expected.push(model_user_text("what next?"));
    runtime.assert_last_model_message_update(&action, update, &expected);
    action
}

/// Checks the whole model input of the first Turn after a compaction.
fn assert_rebuilt_input(runtime: &mut SynchronousRuntime, history: Vec<Value>) -> Value {
    assert_next_turn_input(runtime, "turn-after-compaction", "replace", history)
}

fn invalid_command(message: &str) -> Value {
    json!({"code": "invalid_command", "error": {
        "code": "invalid_command", "message": message,
        "retryable": false, "details": null,
    }})
}

/// Text of about `tokens` estimated tokens.
fn text_of_tokens(tokens: usize) -> String {
    "k".repeat(tokens * 4)
}

/// *Prepare*: An idle Session.
/// *Do*: Set instructions, then start a Turn.
/// *Assert*: The Turn's model input holds the note with the key and the text,
/// before the user message.
#[test]
fn an_upsert_delivers_a_keyed_note() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());

    // Do
    runtime.apply(upsert("model", "You are Mistral Large."), idle());

    // Assert
    assert_next_turn_input(
        &mut runtime,
        "first",
        "replace",
        vec![model_system(), note("model", "You are Mistral Large.")],
    );
}

/// *Prepare*: Instructions, then a finished Turn.
/// *Do*: Replace the instructions with new text, then with that same text
/// again, then start a Turn.
/// *Assert*: The Turn appends the last reply, one overwrite note for each
/// upsert, and the user message: an upsert with the live text is still
/// delivered, and says the previous text no longer applies.
#[test]
fn a_replacement_and_an_identical_upsert_are_both_delivered() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");

    // Do
    runtime.apply(upsert("repository", "Repository: acme/api"), idle());
    runtime.apply(upsert("repository", "Repository: acme/api"), idle());

    // Assert
    assert_next_turn_input(
        &mut runtime,
        "second",
        "append",
        vec![
            model_assistant_text("shipped"),
            overwrite_note("repository", "Repository: acme/api"),
            overwrite_note("repository", "Repository: acme/api"),
        ],
    );
}

/// *Prepare*: Instructions for one key, then a finished Turn.
/// *Do*: Set instructions for a second key, then start a Turn.
/// *Assert*: The new key gets the plain note, not the overwrite note.
#[test]
fn an_upsert_of_a_new_key_sends_the_plain_note() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");

    // Do
    runtime.apply(upsert("plan", "Plan: draft"), idle());

    // Assert
    assert_next_turn_input(
        &mut runtime,
        "second",
        "append",
        vec![model_assistant_text("shipped"), note("plan", "Plan: draft")],
    );
}

/// *Prepare*: A Turn waiting on its completion, with instructions set during
/// it and not yet committed.
/// *Do*: Set the same key again, finish the Turn, then start a Turn.
/// *Assert*: The second upsert sends the overwrite note: instructions waiting
/// for the Turn are live.
#[test]
fn an_upsert_over_instructions_waiting_for_the_turn_sends_the_overwrite_note() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn("first", "open the pull request");
    runtime.apply(
        upsert("review", "Review: requested"),
        running("first").keep(&completion),
    );

    // Do
    runtime.apply(
        upsert("review", "Review: approved"),
        running("first").keep(&completion),
    );
    runtime.finish_turn_with_text("first", &completion, "opened");

    // Assert
    assert_next_turn_input(
        &mut runtime,
        "second",
        "append",
        vec![
            model_assistant_text("opened"),
            note("review", "Review: requested"),
            overwrite_note("review", "Review: approved"),
        ],
    );
}

/// *Prepare*: A running completion, a steer, then instructions that wait in the
/// queue behind it, with a restore between.
/// *Do*: Set the same key again, then let the completion end.
/// *Assert*: The second upsert sends the overwrite note: instructions waiting
/// behind steering are live, and the follow-up completion reads the steer,
/// then both notes, in arrival order.
#[test]
fn an_upsert_over_instructions_waiting_behind_steering_sends_the_overwrite_note() {
    // Prepare
    let turn_id = "first";
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "open the pull request");
    runtime.receive_steering(turn_id, "target the api repository", &[&completion]);
    runtime.apply(
        upsert("review", "Review: requested"),
        running(turn_id).keep(&completion),
    );
    runtime.restart_from_checkpoint();

    // Do
    runtime.apply(
        upsert("review", "Review: approved"),
        running(turn_id).keep(&completion),
    );
    let follow_up = runtime
        .apply(
            text_completion(&completion, "drafted"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(turn_id, &completion, "drafted"))
                .observe(turn_steered(turn_id, "target the api repository")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &follow_up,
        "replace",
        &[
            model_system(),
            model_user_text("open the pull request"),
            model_assistant_text("drafted"),
            model_user_text("target the api repository"),
            note("review", "Review: requested"),
            overwrite_note("review", "Review: approved"),
        ],
    );
}

/// *Prepare*: Instructions, then their removal, then a finished Turn.
/// *Do*: Set the removed key again, then start a Turn.
/// *Assert*: The upsert sends the plain note: a removed key holds no text.
#[test]
fn an_upsert_after_a_delete_sends_the_plain_note() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    runtime.apply(delete("repository"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");

    // Do
    runtime.apply(upsert("repository", "Repository: acme/api"), idle());

    // Assert
    assert_next_turn_input(
        &mut runtime,
        "second",
        "append",
        vec![
            model_assistant_text("shipped"),
            note("repository", "Repository: acme/api"),
        ],
    );
}

/// *Prepare*: Instructions, an overwrite of them before a finished Turn, and
/// an overwrite of another key's after its reply, with a restore between.
/// *Do*: Compact manually, then start a Turn.
/// *Assert*: Both keys are carried with the plain note for their newest text:
/// the replaced text is gone from the rebuilt history.
#[test]
fn compaction_carries_overwritten_instructions_as_plain_notes() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    runtime.apply(upsert("plan", "Plan: draft"), idle());
    runtime.apply(upsert("repository", "Repository: acme/api"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");
    runtime.apply(upsert("plan", "Plan: final"), idle());
    runtime.restart_from_checkpoint();

    // Do
    compact_manually(&mut runtime, "shipped the release");

    // Assert
    let mut history = rebuilt_head(
        vec![
            note("repository", "Repository: acme/api"),
            note("plan", "Plan: final"),
        ],
        &["ship it"],
        "shipped the release",
    );
    history.push(model_assistant_text("shipped"));
    assert_rebuilt_input(&mut runtime, history);
}

/// *Prepare*: Instructions, then a finished Turn.
/// *Do*: Remove the key while idle, then start a Turn.
/// *Assert*: The Turn appends the last reply, the note that the instructions
/// no longer apply and the user message: nothing before them changes.
#[test]
fn a_delete_appends_a_note_that_the_instructions_no_longer_apply() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");

    // Do
    runtime.apply(delete("repository"), idle());

    // Assert
    assert_next_turn_input(
        &mut runtime,
        "second",
        "append",
        vec![model_assistant_text("shipped"), deletion_note("repository")],
    );
}

/// *Prepare*: An idle Session with a removed key.
/// *Do*: Remove a key never set, and the removed key again.
/// *Assert*: Both are accepted and change nothing.
#[test]
fn deleting_a_key_that_is_not_live_changes_nothing() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    runtime.apply(delete("repository"), idle());

    for key in ["never-set", "repository"] {
        let before = runtime.checkpoint_value();

        // Do
        runtime.apply(delete(key), idle());

        // Assert
        assert_eq!(runtime.checkpoint_value(), before);
    }
}

/// *Prepare*: A new Session.
/// *Do*: Set instructions for two keys before any Turn, as a Runtime does
/// right after creation, then start the first Turn.
/// *Assert*: Its model input holds the system prompt, then a note for each key
/// in the order set, then the user message.
#[test]
fn instructions_set_before_the_first_turn_are_delivered_in_order() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());

    // Do
    runtime.apply(upsert("model", "You are Mistral Large."), idle());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());

    // Assert
    assert_next_turn_input(
        &mut runtime,
        "first",
        "replace",
        vec![
            model_system(),
            note("model", "You are Mistral Large."),
            note("repository", "Repository: acme/web"),
        ],
    );
}

/// *Prepare*: Two finished Turns; between them, instructions and an unkeyed
/// context message.
/// *Do*: Compact manually, then start a Turn.
/// *Assert*: The model input is the system prompt, the note, the preamble with
/// the carried user messages, the summary and the last reply. The context
/// message is left to the summary.
#[test]
fn instructions_are_carried_right_after_the_system_prompt() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    run_turn(&mut runtime, "first", "deploy the api", "deploying");
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    runtime.apply(context_message("the build cache is warm"), idle());
    run_turn(&mut runtime, "second", "check the status", "status checked");

    // Do
    compact_manually(&mut runtime, "the api is deployed");

    // Assert
    let mut history = rebuilt_head(
        vec![note("repository", "Repository: acme/web")],
        &["deploy the api", "check the status"],
        "the api is deployed",
    );
    history.push(model_assistant_text("status checked"));
    assert_rebuilt_input(&mut runtime, history);
}

/// *Prepare*: Instructions for two keys before the last reply, the first key
/// set twice.
/// *Do*: Compact manually, then start a Turn.
/// *Assert*: Each key is carried once with its newest text, in the order of
/// their last update.
#[test]
fn each_live_key_is_carried_once_with_its_newest_text() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    runtime.apply(upsert("plan", "Plan: draft"), idle());
    runtime.apply(upsert("repository", "Repository: acme/api"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");

    // Do
    compact_manually(&mut runtime, "shipped the release");

    // Assert
    let mut history = rebuilt_head(
        vec![
            note("plan", "Plan: draft"),
            note("repository", "Repository: acme/api"),
        ],
        &["ship it"],
        "shipped the release",
    );
    history.push(model_assistant_text("shipped"));
    assert_rebuilt_input(&mut runtime, history);
}

/// *Prepare*: Instructions before a finished Turn, then two upserts for the
/// same key after its reply.
/// *Do*: Compact manually, then start a Turn.
/// *Assert*: Only the newest note stays, moved right after the system prompt;
/// no note is left after the reply.
#[test]
fn upserts_after_the_last_reply_move_after_the_system_prompt_once() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");
    runtime.apply(upsert("repository", "Repository: acme/api"), idle());
    runtime.apply(upsert("repository", "Repository: acme/cli"), idle());

    // Do
    compact_manually(&mut runtime, "shipped from web");

    // Assert
    let mut history = rebuilt_head(
        vec![note("repository", "Repository: acme/cli")],
        &["ship it"],
        "shipped from web",
    );
    history.push(model_assistant_text("shipped"));
    assert_rebuilt_input(&mut runtime, history);
}

/// *Prepare*: Instructions, a finished Turn, a removal of the key, and a
/// second finished Turn.
/// *Do*: Compact manually, then start a Turn.
/// *Assert*: Neither the removed instructions nor the removal note is carried.
#[test]
fn a_removed_key_leaves_nothing_after_compaction() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");
    runtime.apply(delete("repository"), idle());
    run_turn(&mut runtime, "second", "clean up", "cleaned up");

    // Do
    compact_manually(&mut runtime, "shipped and cleaned up");

    // Assert
    let mut history = rebuilt_head(vec![], &["ship it", "clean up"], "shipped and cleaned up");
    history.push(model_assistant_text("cleaned up"));
    assert_rebuilt_input(&mut runtime, history);
}

/// *Prepare*: Instructions for three keys before a finished Turn; after its
/// reply, the first key set again, the second removed, and a new key set.
/// *Do*: Compact manually, then start a Turn.
/// *Assert*: The live keys follow the system prompt in the order they were
/// last updated, each once with its newest text. The removed key and every
/// note after the reply are gone.
#[test]
fn live_keys_follow_the_system_prompt_in_the_order_of_their_last_update() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    runtime.apply(upsert("review", "Review: requested"), idle());
    runtime.apply(upsert("plan", "Plan: draft"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");
    runtime.apply(upsert("repository", "Repository: acme/api"), idle());
    runtime.apply(delete("review"), idle());
    runtime.apply(upsert("owner", "Owner: platform"), idle());

    // Do
    compact_manually(&mut runtime, "shipped the release");

    // Assert
    let mut history = rebuilt_head(
        vec![
            note("plan", "Plan: draft"),
            note("repository", "Repository: acme/api"),
            note("owner", "Owner: platform"),
        ],
        &["ship it"],
        "shipped the release",
    );
    history.push(model_assistant_text("shipped"));
    assert_rebuilt_input(&mut runtime, history);
}

/// *Prepare*: A Turn waiting on its completion; instructions set and then
/// removed during it, with a checkpoint restore between.
/// *Do*: Finish the Turn, which commits both notes after its reply. Compact,
/// then start a Turn.
/// *Assert*: Both notes waited across the restore, after the reply. The
/// compaction drops both, though they follow the last reply.
#[test]
fn a_removal_after_the_last_reply_leaves_nothing_after_compaction() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let turn_id = "first";
    let completion = runtime.start_turn(turn_id, "open the pull request");
    runtime.apply(
        upsert("review", "Review: requested"),
        running(turn_id).keep(&completion),
    );
    runtime.restart_from_checkpoint();
    runtime.apply(delete("review"), running(turn_id).keep(&completion));
    runtime.finish_turn_with_text(turn_id, &completion, "opened");
    let before_compaction = runtime.checkpoint_value();
    let messages = before_compaction["context"]["messages"]
        .as_array()
        .expect("the history is a list");
    assert_eq!(
        messages[messages.len() - 2..]
            .iter()
            .map(|stored| stored["instructions"].clone())
            .collect::<Vec<_>>(),
        [
            json!({"kind": "upsert", "key": "review"}),
            json!({"kind": "delete", "key": "review"}),
        ]
    );

    // Do
    compact_manually(&mut runtime, "first summary");

    // Assert
    let mut history = rebuilt_head(vec![], &["open the pull request"], "first summary");
    history.push(model_assistant_text("opened"));
    assert_rebuilt_input(&mut runtime, history);
}

/// *Prepare*: Instructions, their removal, new instructions for the same key,
/// and a finished Turn.
/// *Do*: Compact manually, then start a Turn.
/// *Assert*: The new instructions are carried after the system prompt.
#[test]
fn a_key_set_again_after_its_removal_is_carried() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    runtime.apply(delete("repository"), idle());
    runtime.apply(upsert("repository", "Repository: acme/api"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");

    // Do
    compact_manually(&mut runtime, "shipped the release");

    // Assert
    let mut history = rebuilt_head(
        vec![note("repository", "Repository: acme/api")],
        &["ship it"],
        "shipped the release",
    );
    history.push(model_assistant_text("shipped"));
    assert_rebuilt_input(&mut runtime, history);
}

/// *Prepare*: Instructions set while a Turn waits on its completion, then a
/// checkpoint restore before the completion finishes.
/// *Do*: Finish the Turn, run a second Turn, compact, restore, run a third
/// Turn, and compact again.
/// *Assert*: The note waits for the next Turn across the restore, is
/// delivered there, and every compaction carries it again after the system
/// prompt.
#[test]
fn instructions_set_during_a_turn_wait_and_survive_restores_and_compactions() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let turn_id = "first";
    let completion = runtime.start_turn(turn_id, "open the pull request");
    runtime.apply(
        upsert("review", "Review: requested"),
        running(turn_id).keep(&completion),
    );
    runtime.restart_from_checkpoint();
    runtime.finish_turn_with_text(turn_id, &completion, "opened");

    // Do / Assert
    let action = assert_next_turn_input(
        &mut runtime,
        "second",
        "replace",
        vec![
            model_system(),
            model_user_text("open the pull request"),
            model_assistant_text("opened"),
            note("review", "Review: requested"),
        ],
    );
    runtime.finish_turn_with_text("second", &action, "reviewed");
    compact_manually(&mut runtime, "first summary");
    runtime.restart_from_checkpoint();
    run_turn(&mut runtime, "third", "merge it", "merged");
    compact_manually(&mut runtime, "second summary");

    let mut history = rebuilt_head(
        vec![note("review", "Review: requested")],
        &["open the pull request", "what next?", "merge it"],
        "second summary",
    );
    history.push(model_assistant_text("merged"));
    assert_rebuilt_input(&mut runtime, history);
}

/// *Prepare*: An idle Session.
/// *Do*: Set and remove instructions with an empty key, a key over the limit
/// and a key a tag attribute would need escaped, and set empty instructions.
/// *Assert*: Core rejects each without changing the Session.
#[test]
fn instructions_keys_and_content_are_validated() {
    let mut runtime = SynchronousRuntime::new(config());
    for (key, message) in [
        (String::new(), "instructions key must not be empty"),
        (
            "k".repeat(129),
            "instructions key must be at most 128 characters",
        ),
        (
            "say \"hi\"".to_string(),
            "instructions key may contain only ASCII letters, digits, '.', '_', ':' and '-'",
        ),
    ] {
        runtime.reject(upsert(&key, "text"), invalid_command(message));
        runtime.reject(delete(&key), invalid_command(message));
    }
    runtime.reject(
        upsert("model", "  "),
        invalid_command("instructions content must not be empty"),
    );
}

/// *Do*: Decode a `context_message` with a `key` and a `context_delete`.
/// *Assert*: Neither is a Step Protocol command: context messages carry no key.
#[test]
fn context_messages_carry_no_key_and_have_no_delete() {
    for (command, error) in [
        (
            json!({"type": "context_message", "key": "repository",
                   "content": [{"type": "text", "text": "branch: main"}]}),
            "unknown field `key`",
        ),
        (
            json!({"type": "context_delete", "key": "repository"}),
            "unknown variant `context_delete`",
        ),
    ] {
        let decoded = serde_json::from_value::<HarnessInput>(json!({
            "protocol_version": 1,
            "input_id": 1,
            "determinism": {"time_unix_ms": 0, "random_seed": 1},
            "command": command,
        }));
        let detail = decoded.expect_err("the command is refused").to_string();
        assert!(detail.contains(error), "{detail}");
    }
}

/// *Prepare*: An idle Session with the default limit of 16,000 tokens.
/// *Do*: Set instructions of about 17,500 tokens, then send the same text as
/// a context message.
/// *Assert*: Core rejects the instructions without storing them, and admits
/// the context message: context messages are not bounded.
#[test]
fn instructions_over_the_limit_are_rejected() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let text = text_of_tokens(17_500);

    // Do / Assert
    runtime.reject(
        upsert("repository", &text),
        invalid_command(
            "instructions cannot be admitted: the instructions would take about \
             17532 tokens, over the limit of 16000",
        ),
    );
    runtime.apply(context_message(&text), idle());
}

/// *Prepare*: Instructions of about 10,000 and 5,000 tokens under two keys.
/// *Do*: Set about 10,000 tokens again under the first key.
/// *Assert*: Core admits it: the new text replaces the old one in the live
/// set, which stays at about 15,000 tokens.
#[test]
fn a_replaced_key_no_longer_counts_toward_the_limit() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", &text_of_tokens(10_000)), idle());
    runtime.apply(upsert("plan", &text_of_tokens(5_000)), idle());

    // Do / Assert
    runtime.apply(upsert("repository", &text_of_tokens(10_000)), idle());
}

/// *Prepare*: About 10,000 tokens of instructions, then their removal.
/// *Do*: Set about 10,000 tokens under another key.
/// *Assert*: Core admits it: a removed key no longer counts toward the limit,
/// and neither does its removal note.
#[test]
fn a_removed_key_no_longer_counts_toward_the_limit() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", &text_of_tokens(10_000)), idle());
    runtime.apply(delete("repository"), idle());

    // Do / Assert
    runtime.apply(upsert("plan", &text_of_tokens(10_000)), idle());
}

/// *Prepare*: A Turn waiting on its completion, with about 10,000 tokens of
/// instructions set during it and not yet committed.
/// *Do*: Set another 10,000 tokens under a second key.
/// *Assert*: Core rejects it: instructions waiting for the Turn count toward
/// the limit.
#[test]
fn instructions_waiting_for_the_turn_count_toward_the_limit() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let turn_id = "first";
    let completion = runtime.start_turn(turn_id, "open the pull request");
    runtime.apply(
        upsert("review", &text_of_tokens(10_000)),
        running(turn_id).keep(&completion),
    );

    // Do / Assert
    runtime.reject(
        upsert("plan", &text_of_tokens(10_000)),
        invalid_command(
            "instructions cannot be admitted: the instructions would take about \
             20060 tokens, over the limit of 16000",
        ),
    );
}

/// Automatic compaction at a 40,000-token threshold, with an instructions
/// limit as high as the threshold, so only the room for a summary bounds it.
fn compaction_config_without_an_instructions_limit() -> HarnessConfig {
    let mut value = serde_json::to_value(automatic_compaction_config()).expect("config serializes");
    value["settings"]["context"]["instructions"] =
        json!({"max_tokens": 1_000_000, "max_threshold_percent": 100});
    serde_json::from_value(value).expect("instructions settings are valid")
}

/// *Prepare*: An idle Session with automatic compaction at 40,000 tokens and
/// an instructions limit as high as the threshold.
/// *Do*: Set instructions of about 39,000 tokens.
/// *Assert*: Core rejects them: with them, a rebuilt history has no room for a
/// summary under the threshold, so every compaction would fail.
#[test]
fn instructions_that_leave_no_room_for_a_summary_are_rejected() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(compaction_config_without_an_instructions_limit());

    // Do / Assert
    runtime.reject(
        upsert("repository", &text_of_tokens(39_000)),
        invalid_command(
            "instructions cannot be admitted: the instructions would take about \
             39032 tokens and leave no room for a compaction summary under the \
             compaction threshold of 40000",
        ),
    );
}

/// *Prepare*: An idle Session without automatic compaction and about 10,000
/// tokens of instructions, under the 16,000-token limit.
/// *Do*: Reconfigure to automatic compaction at 40,000 tokens, whose limit is
/// 10% of the threshold, then remove the instructions.
/// *Assert*: Core accepts both: the limit only bounds instructions that grow,
/// and the instructions still leave room for a summary.
#[test]
fn settings_under_which_instructions_exceed_the_limit_are_accepted() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", &text_of_tokens(10_000)), idle());

    // Do / Assert
    runtime.reconfigure_settings(automatic_compaction_config().settings, idle());
    runtime.apply(delete("repository"), idle());
}

/// *Prepare*: An idle Session without automatic compaction and about 15,500
/// tokens of instructions, under the 16,000-token limit.
/// *Do*: Reconfigure to automatic compaction at 16,000 tokens.
/// *Assert*: Core rejects the reconfiguration and keeps the Session as it
/// was: the instructions leave no room for a summary, so every compaction
/// would fail.
#[test]
fn settings_under_which_instructions_leave_no_room_for_a_summary_are_rejected() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", &text_of_tokens(15_500)), idle());

    // Do / Assert
    runtime.reject(
        json!({
            "type": "reconfigure",
            "changes": [{
                "type": "settings",
                "value": automatic_compaction_config_with_threshold(16_000).settings,
            }],
        }),
        invalid_command(
            "the Session's context cannot move to a model with a 16001-token context window \
             and a compaction threshold of 16000: compaction summary and required \
             continuation context cannot fit the configured token threshold",
        ),
    );
    runtime.apply(delete("repository"), idle());
}

/// *Prepare*: Instructions, then a manual compaction waiting on its summary.
/// *Do*: Set and remove instructions.
/// *Assert*: Core refuses both while the compaction is pending.
#[test]
fn instructions_cannot_change_while_compaction_is_pending() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    runtime.apply(upsert("repository", "Repository: acme/web"), idle());
    run_turn(&mut runtime, "first", "ship it", "shipped");
    runtime.apply(
        compact(""),
        compacting("manual").dispatch(compaction_call("manual", 0, 1)),
    );

    // Do / Assert
    for (command, command_type) in [
        (
            upsert("repository", "Repository: acme/api"),
            "instructions_upsert",
        ),
        (delete("repository"), "instructions_delete"),
    ] {
        runtime.reject(
            command,
            json!({"code": "invalid_state", "command_type": command_type, "state": "compacting"}),
        );
    }
}
