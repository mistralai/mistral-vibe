//! `/clear` adopts the replacement session and names the previous one only when it can be resumed.

use vibe_rs::app::{App, QueuedPrompt};
use vibe_rs::commands::clear::{apply_cleared, cleared, new_conversation_text};
use vibe_rs::commands::CommandEvent;
use vibe_rs::server::TokenUsage;

#[test]
fn clearing_adopts_the_replacement_session() {
    let result = serde_json::json!({
        "state": {
            "eventId": 1,
            "session": {
                "id": "new-session",
                "tokenUsage": {"inputTokens": 30, "outputTokens": 12, "totalTokens": 42},
            },
            "history": []
        },
    });
    let seed = QueuedPrompt {
        message_id: "rs-1".into(),
        text: "hi".into(),
    };
    let CommandEvent::Cleared {
        session_id,
        usage,
        seed,
        ..
    } = cleared(result, None, Some(seed))
    else {
        panic!("expected a cleared event");
    };
    assert_eq!(session_id, "new-session");
    assert_eq!(seed.map(|seed| seed.text).as_deref(), Some("hi"));

    let mut app = App::default();
    app.session.session_id = Some("dead-session".into());
    app.session.tokens = (1234, 200000);
    app.subagents.main_tokens = app.session.tokens;
    app.session.active_turn_id = Some("turn-1".into());
    app.session.usage_baseline = Some(TokenUsage {
        input_tokens: 1000,
        output_tokens: 500,
        total_tokens: 0,
    });
    apply_cleared(&mut app, session_id, usage, Vec::new());
    assert_eq!(app.session.session_id.as_deref(), Some("new-session"));
    assert!(app.session.active_turn_id.is_none());
    // The gauge resets instead of waiting for the fresh session's first stats.
    assert_eq!(app.session.tokens, (0, 200000));
    assert_eq!(app.subagents.main_tokens, app.session.tokens);
    // The clear adopt restarts the usage delta from the fresh session.
    assert_eq!(
        app.session.usage_baseline,
        Some(TokenUsage {
            input_tokens: 30,
            output_tokens: 12,
            total_tokens: 42
        })
    );
}

#[test]
fn a_response_without_state_is_an_error() {
    assert!(matches!(
        cleared(serde_json::json!({}), None, None),
        CommandEvent::Error(_)
    ));
}

#[test]
fn a_resumable_previous_session_is_named_with_its_resume_command() {
    assert_eq!(
        new_conversation_text(Some("0123456789abcdef")),
        "New conversation started.\n\nPrevious session: `01234567`\n\
         To resume it later, run: `vibe --resume 01234567`"
    );
}

#[test]
fn a_non_resumable_previous_session_is_not_mentioned() {
    assert_eq!(new_conversation_text(None), "New conversation started.");
}

#[test]
fn a_seed_prompt_names_its_pasted_images_by_path() {
    let mut app = App::default();
    let label = app.chat_input.pasted_images.register("/tmp/shot.png", 0);

    let seed = vibe_rs::commands::clear::seed(&app, &format!("/clear describe {label}"));

    assert_eq!(
        seed.map(|seed| seed.text).as_deref(),
        Some("describe @/tmp/shot.png")
    );
    assert!(vibe_rs::commands::clear::seed(&app, "/clear").is_none());
    assert!(vibe_rs::commands::clear::seed(&app, "/clear   ").is_none());
}
