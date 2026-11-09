use super::*;
use crate::core::prompt::PromptSettings;
use crate::core::wire::content::ResourceContents;
use crate::core::wire::message::{
    AssistantPart, AssistantSemanticPart, ToolArguments, ToolOutcome,
};

const TRUNCATION_MARKER_END: &str = " tokens truncated here for size</system>";

fn prompts() -> Prompts {
    PromptSettings::default().compaction
}

fn budget(token_threshold: Option<u64>) -> CompactionBudget {
    CompactionBudget {
        token_threshold,
        image_delivery: ImageDeliveryMode::Native,
        image_cost: crate::core::testing::test_model_facts().image_cost,
    }
}

fn system(text: impl Into<String>) -> StoredMessage {
    StoredMessage::generated_system(Message::system_text(text.into()))
}

fn user(text: impl Into<String>) -> StoredMessage {
    StoredMessage::visible(Message::user_text(text.into()))
}

fn reply(text: &str, calls: &[(&str, &str)]) -> StoredMessage {
    let mut parts = vec![AssistantPart::Content(ContentBlock::text(text))];
    parts.extend(calls.iter().map(|(id, name)| {
        AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
            id: id.to_string(),
            name: name.to_string(),
            arguments: ToolArguments::Json {
                raw: "{}".to_string(),
                value: serde_json::json!({}),
            },
            meta: None,
        })
    }));
    StoredMessage::visible(Message::assistant(parts))
}

fn result(id: &str, name: &str, text: impl Into<String>) -> StoredMessage {
    StoredMessage::visible(Message::tool(
        id.to_string(),
        name.to_string(),
        ToolOutcome::Success,
        vec![ContentBlock::text(text.into())],
        None,
    ))
}

/// A request and an earlier exchange the summary covers, then the last
/// reply with two tool results of about 10k tokens each, then a steer.
fn history(request: &str, steer: &str) -> Vec<StoredMessage> {
    vec![
        system("agent system prompt"),
        user(request),
        reply("Reading.", &[("call-0", "read_file")]),
        result("call-0", "read_file", "b".repeat(8_000)),
        reply("Running both.", &[("call-1", "bash"), ("call-2", "grep")]),
        result("call-1", "bash", "c".repeat(40_000)),
        result("call-2", "grep", "d".repeat(40_000)),
        user(steer),
    ]
}

fn rebuild_with(
    canonical: &[StoredMessage],
    system_message: StoredMessage,
    summary: Summary<'_>,
    token_threshold: Option<u64>,
) -> Option<Rebuilt> {
    let prompts = prompts();
    fitted_replacement(
        canonical,
        system_message,
        summary,
        &[],
        budget(token_threshold),
        &RebuildTexts::new(&prompts),
    )
    .expect("the rebuild is estimated")
}

fn rebuild(
    canonical: &[StoredMessage],
    summary: &str,
    token_threshold: Option<u64>,
) -> Option<Rebuilt> {
    rebuild_with(
        canonical,
        system("agent system prompt"),
        Summary::Complete(summary),
        token_threshold,
    )
}

fn rebuild_cut_off(
    canonical: &[StoredMessage],
    partial: &str,
    token_threshold: Option<u64>,
) -> Option<Rebuilt> {
    rebuild_with(
        canonical,
        system("agent system prompt"),
        Summary::CutOff(partial),
        token_threshold,
    )
}

fn size(messages: &[StoredMessage]) -> u64 {
    estimate(messages, &[], budget(None)).expect("the messages are estimated")
}

fn text(stored: &StoredMessage) -> String {
    let Message::User { content, .. } = &stored.message else {
        panic!("a user message: {stored:?}");
    };
    content_blocks_to_text(content, "user message").expect("the message has text")
}

fn reduction(rebuilt: &Rebuilt) -> &Reduction {
    rebuilt.reduction.as_ref().expect("the rebuild is reduced")
}

/// The summary text the rebuilt history holds, between its delimiters.
fn held_summary(rebuilt: &Rebuilt) -> String {
    let message = rebuilt
        .messages
        .iter()
        .map(text_or_empty)
        .find(|text| text.contains(COMPACTION_SUMMARY_OPEN))
        .expect("the rebuild holds the summary");
    message
        .split(&format!("{COMPACTION_SUMMARY_OPEN}\n"))
        .nth(1)
        .and_then(|rest| rest.strip_suffix(&format!("\n{COMPACTION_SUMMARY_CLOSE}")))
        .expect("the summary is delimited")
        .to_string()
}

fn text_or_empty(stored: &StoredMessage) -> String {
    match &stored.message {
        Message::User { content, .. } => {
            content_blocks_to_text(content, "user message").unwrap_or_default()
        }
        _ => String::new(),
    }
}

fn summary_lines(count: usize) -> String {
    (0..count)
        .map(|line| format!("summary line {line}"))
        .collect::<Vec<_>>()
        .join("\n")
}

/// The tokens a cut summary may take: the share of the room the fixed
/// parts, the note and the omitted line leave.
fn cut_share(canonical: &[StoredMessage], token_threshold: u64) -> u64 {
    let note = noted("", &prompts().summary_cut_note);
    let (summarized, _) = split_at_last_reply(canonical);
    let carried = collect_user_messages(
        summarized,
        carry_tokens(
            &system("agent system prompt"),
            &[],
            budget(Some(token_threshold)),
        )
        .expect("the carry is estimated"),
        ImageDeliveryMode::Native,
        budget(None).image_cost,
    )
    .expect("the carry is collected");
    let mut minimal = head(system("agent system prompt"), &carried, &note);
    minimal.push(view_message(&prompts().reduced_view_omitted));
    (token_threshold - size(&minimal) - 1) * TRUNCATED_SUMMARY_SHARE / 100
}

fn text_tokens(text: &str) -> u64 {
    size(&[view_message(text)]) - size(&[view_message("")])
}

#[test]
fn without_a_threshold_everything_after_the_last_reply_goes_back() {
    let canonical = history("Fix the parser", "also check the tests");
    let rebuilt = rebuild(&canonical, "summary", None).expect("no threshold always fits");

    assert!(rebuilt.reduction.is_none());
    assert!(rebuilt.summary_cut.is_none());
    assert_eq!(rebuilt.messages[2].message, canonical[1].message);
    assert_eq!(&rebuilt.messages[4..], &canonical[4..]);
}

#[test]
fn a_rebuild_under_the_threshold_is_not_reduced() {
    let canonical = history("Fix the parser", "also check the tests");
    let whole = rebuild(&canonical, "summary", None).expect("fits").messages;

    let rebuilt = rebuild(&canonical, "summary", Some(size(&whole) + 1)).expect("the rebuild fits");

    assert!(rebuilt.reduction.is_none());
    assert!(rebuilt.summary_cut.is_none());
    assert_eq!(rebuilt.messages, whole);
}

#[test]
fn the_carried_messages_stay_whole_while_the_exchange_is_reduced() {
    let mut canonical = history("Fix the parser", "also check the tests");
    canonical.insert(1, user("First, read the grammar"));
    let whole = rebuild(&canonical, "summary", None).expect("fits").messages;

    let rebuilt =
        rebuild(&canonical, "summary", Some(size(&whole) - 12_000)).expect("the rebuild fits");

    assert_eq!(reduction(&rebuilt).reduced_messages, 3);
    assert_eq!(rebuilt.messages[2], canonical[1]);
    assert_eq!(rebuilt.messages[3], canonical[2]);
    assert!(held_summary(&rebuilt) == "summary");
}

#[test]
fn the_carry_is_at_most_a_quarter_of_the_room_and_at_most_20k_tokens() {
    let request = format!("Fix the parser {}", "a".repeat(400_000));
    let canonical = history(&request, "also check the tests");

    let unbounded = rebuild(&canonical, "summary", None).expect("no threshold always fits");
    let carried = text(&unbounded.messages[2]);
    assert!(carried.starts_with("Fix the parser"));
    assert!(carried.contains(TRUNCATION_MARKER_END));
    assert!(text_tokens(&carried) <= USER_CARRY_TOKENS as u64 + 20);

    let preamble = size(&head(system("agent system prompt"), &[], "")[..2]);
    let rebuilt = rebuild(&canonical, "summary", Some(40_000)).expect("the rebuild fits");
    let carried = text(&rebuilt.messages[2]);
    assert!(carried.starts_with("Fix the parser"));
    assert!(carried.contains(TRUNCATION_MARKER_END));
    let quarter = (40_000 - preamble) / 4;
    assert!(
        text_tokens(&carried) <= quarter + 20,
        "{}",
        text_tokens(&carried)
    );
    assert!(
        text_tokens(&carried) >= quarter - 20,
        "{}",
        text_tokens(&carried)
    );
}

fn image_request() -> StoredMessage {
    StoredMessage::visible(Message::user(vec![
        ContentBlock::text("Fix what this screenshot shows"),
        ContentBlock::image("aW1hZ2U=", "image/png"),
    ]))
}

#[test]
fn a_text_message_is_left_out_of_a_carry_with_no_budget() {
    let image_cost = budget(None).image_cost;
    let collect = |messages: &[StoredMessage]| {
        collect_user_messages(messages, 0, ImageDeliveryMode::Native, image_cost)
            .expect("the carry is collected")
    };

    assert!(collect(&[user("Fix the parser")]).is_empty());
    assert_eq!(collect(&[image_request()]), vec![image_request()]);
}

#[test]
fn a_cut_message_stays_within_the_carry_budget_with_its_framing() {
    let image_cost = budget(None).image_cost;
    let estimate = |messages: &[StoredMessage]| {
        messages
            .iter()
            .map(|stored| {
                estimate_user_message_tokens(stored, ImageDeliveryMode::Native, image_cost)
                    .expect("the message is estimated")
            })
            .sum::<usize>()
    };
    let requests = [
        user(format!("Fix the parser {}", "a".repeat(4_000))),
        user(format!("Fix the parser {}", "\"\n".repeat(2_000))),
        user("\u{0}".repeat(8_000)),
    ];

    for request in requests {
        for max_tokens in [1, 5, 12, 30, 64, 200] {
            let carried = collect_user_messages(
                std::slice::from_ref(&request),
                max_tokens,
                ImageDeliveryMode::Native,
                image_cost,
            )
            .expect("the carry is collected");
            assert_eq!(carried.len(), usize::from(max_tokens >= 30));
            assert!(
                estimate(&carried) <= max_tokens,
                "{} > {max_tokens}",
                estimate(&carried)
            );
        }
    }
}

#[test]
fn an_image_over_the_carry_budget_is_carried_whole_when_the_rebuild_fits() {
    let mut canonical = history("unused", "also check the tests");
    canonical[1] = image_request();
    let token_threshold = 20_000;
    let carry = carry_tokens(
        &system("agent system prompt"),
        &[],
        budget(Some(token_threshold)),
    )
    .expect("the carry is estimated");
    assert!(
        estimate_user_message_tokens(
            &canonical[1],
            ImageDeliveryMode::Native,
            budget(None).image_cost
        )
        .expect("the image is estimated")
            > carry
    );

    let rebuilt = rebuild(&canonical, "summary", Some(token_threshold)).expect("the rebuild fits");

    assert_eq!(rebuilt.messages[2], canonical[1]);
    assert!(rebuilt.reduction.is_some());
    assert!(size(&rebuilt.messages) < token_threshold);
}

/// *Prepare*: A request holding a long text file as an embedded resource,
/// the reply to it, and a later short request.
/// *Do*: Rebuild under a threshold the file is over.
/// *Assert*: The file is not an image: it is left to the summary like long
/// text, and the rebuild fits.
#[test]
fn an_embedded_text_resource_over_the_carry_budget_is_left_to_the_summary() {
    let report = StoredMessage::visible(Message::user(vec![ContentBlock::resource(
        ResourceContents::TextResourceContents {
            uri: "file:///tmp/report.txt".to_string(),
            mime_type: Some("text/plain".to_string()),
            text: "word ".repeat(60_000),
            meta: None,
        },
    )]));
    let canonical = vec![
        system("agent system prompt"),
        report.clone(),
        reply("Noted.", &[]),
        user("Summarize it."),
        user("Give the answer."),
    ];
    let image_cost = budget(None).image_cost;

    assert!(
        collect_user_messages(&[report], 0, ImageDeliveryMode::Native, image_cost)
            .expect("the carry is collected")
            .is_empty()
    );
    let rebuilt = rebuild(&canonical, "summary", Some(28_000)).expect("the rebuild fits");
    assert_eq!(&rebuilt.messages[3..], &canonical[2..]);
    assert!(size(&rebuilt.messages) < 28_000);
}

#[test]
fn an_image_no_rebuild_can_fit_is_refused() {
    let mut canonical = history("unused", "also check the tests");
    canonical[1] = image_request();

    assert!(rebuild(&canonical, "summary", Some(8_000)).is_none());
}

#[test]
fn the_exchange_is_reduced_before_the_later_messages() {
    let canonical = history("Fix the parser", "also check the tests");
    let whole = rebuild(&canonical, "summary", None).expect("fits").messages;

    let rebuilt =
        rebuild(&canonical, "summary", Some(size(&whole) - 12_000)).expect("the rebuild fits");

    assert_eq!(
        reduction(&rebuilt),
        &Reduction {
            reduced_messages: 3,
            view: "clipped",
        }
    );
    assert!(rebuilt.summary_cut.is_none());
    let [.., view, steer] = rebuilt.messages.as_slice() else {
        panic!("the rebuild ends with the view and the steer");
    };
    let view = text(view);
    assert!(view.starts_with(&prompts().reduced_view_header));
    assert!(view.contains("\nbash()\ngrep()\n<OK> ccc"));
    assert!(!view.contains("Running both."));
    assert_eq!(steer, canonical.last().expect("a steer"));
}

#[test]
fn later_messages_are_reduced_when_the_exchange_alone_is_not_enough() {
    let steer = format!("also check the tests {}", "e".repeat(40_000));
    let canonical = history("Fix the parser", &steer);
    let whole = rebuild(&canonical, "summary", None).expect("fits").messages;

    let rebuilt =
        rebuild(&canonical, "summary", Some(size(&whole) - 25_000)).expect("the rebuild fits");

    assert_eq!(
        reduction(&rebuilt),
        &Reduction {
            reduced_messages: 4,
            view: "clipped",
        }
    );
    let view = text(rebuilt.messages.last().expect("the view"));
    assert!(view.contains("\nUserMessage: also check the tests"));
    assert!(view.contains("[CONTENT OMITTED FOR SIZE: "));
}

#[test]
fn a_summary_too_long_for_the_room_is_cut_to_the_share_at_a_line_end_with_the_note() {
    let canonical = history("Fix the parser", "also check the tests");
    let summary = summary_lines(20_000);

    let rebuilt = rebuild(&canonical, &summary, Some(2_000)).expect("the cut summary fits");

    assert!(size(&rebuilt.messages) < 2_000);
    assert_eq!(
        reduction(&rebuilt),
        &Reduction {
            reduced_messages: 4,
            view: OMITTED_LEVEL,
        }
    );
    assert_eq!(
        text(rebuilt.messages.last().expect("the omitted line")),
        prompts().reduced_view_omitted
    );
    assert_eq!(rebuilt.summary, summary);
    let held = held_summary(&rebuilt);
    let cut = held
        .strip_suffix(&noted("", &prompts().summary_cut_note))
        .expect("the note follows the cut summary");
    assert!(summary.starts_with(&format!("{cut}\n")));
    let share = cut_share(&canonical, 2_000);
    let line = text_tokens("\nsummary line 19999");
    assert!(text_tokens(cut) <= share && text_tokens(cut) + line > share);
    assert_eq!(
        rebuilt.summary_cut,
        Some(SummaryCut {
            cause: SummaryCutCause::TooLong,
            summary_bytes: summary.len(),
            kept_bytes: cut.len(),
        })
    );
}

#[test]
fn a_cut_off_summary_that_fits_is_kept_whole_with_the_note() {
    let canonical = history("Fix the parser", "also check the tests");
    let partial = "Fixed the lexer.\nNow on the";
    let whole = rebuild(&canonical, partial, None).expect("fits").messages;

    for token_threshold in [None, Some(size(&whole) + 100)] {
        let rebuilt =
            rebuild_cut_off(&canonical, partial, token_threshold).expect("the rebuild fits");

        assert_eq!(
            held_summary(&rebuilt),
            noted(partial, &prompts().summary_cut_note)
        );
        assert!(rebuilt.reduction.is_none());
        assert_eq!(&rebuilt.messages[4..], &canonical[4..]);
        assert_eq!(
            rebuilt.summary_cut,
            Some(SummaryCut {
                cause: SummaryCutCause::CutOff,
                summary_bytes: partial.len(),
                kept_bytes: partial.len(),
            })
        );
    }
}

#[test]
fn a_long_cut_off_summary_is_cut_to_the_share_and_the_items_after_it_give_way() {
    let canonical = history("Fix the parser", "also check the tests");
    let partial = summary_lines(20_000);

    let rebuilt = rebuild_cut_off(&canonical, &partial, Some(6_000)).expect("the rebuild fits");

    assert!(size(&rebuilt.messages) < 6_000);
    let held = held_summary(&rebuilt);
    let cut = held
        .strip_suffix(&noted("", &prompts().summary_cut_note))
        .expect("the note follows the cut summary");
    assert!(partial.starts_with(&format!("{cut}\n")));
    let share = cut_share(&canonical, 6_000);
    let line = text_tokens("\nsummary line 19999");
    assert!(text_tokens(cut) <= share && text_tokens(cut) + line > share);
    assert_eq!(reduction(&rebuilt).reduced_messages, 3);
    assert_eq!(rebuilt.messages.last(), canonical.last());
    let cut_facts = rebuilt.summary_cut.as_ref().expect("the summary is cut");
    assert_eq!(cut_facts.cause, SummaryCutCause::CutOff);
    assert_eq!(cut_facts.kept_bytes, cut.len());
}

#[test]
fn a_huge_task_message_keeps_its_carry_and_the_new_tool_results_give_way() {
    let request = format!("Fix the parser {}", "a".repeat(400_000));
    let canonical = vec![
        system("agent system prompt"),
        user(&request),
        reply("Running both.", &[("call-1", "bash"), ("call-2", "grep")]),
        result("call-1", "bash", "c".repeat(100_000)),
        result("call-2", "grep", "d".repeat(100_000)),
        user("also check the tests"),
    ];
    let preamble = size(&head(system("agent system prompt"), &[], "")[..2]);
    let quarter = (60_000 - preamble) / 4;

    let rebuilt = rebuild(&canonical, "summary", Some(60_000)).expect("the rebuild fits");

    assert!(size(&rebuilt.messages) < 60_000);
    let carried = text(&rebuilt.messages[2]);
    assert!(carried.starts_with("Fix the parser"));
    assert!(
        text_tokens(&carried) >= quarter - 20,
        "{}",
        text_tokens(&carried)
    );
    assert_eq!(held_summary(&rebuilt), "summary");
    assert_eq!(reduction(&rebuilt).reduced_messages, 3);
    assert_eq!(rebuilt.messages.last(), canonical.last());
}

#[test]
fn fixed_parts_over_the_threshold_cannot_be_rebuilt() {
    let canonical = history("Fix the parser", "also check the tests");
    let huge_system = system("s".repeat(80_000));
    let prompts = prompts();

    assert!(rebuild(&canonical, "summary", Some(15_000)).is_some());
    for summary in [Summary::Complete("summary"), Summary::CutOff("summary")] {
        assert!(rebuild_with(&canonical, huge_system.clone(), summary, Some(15_000)).is_none());
    }
    assert!(
        !minimum_replacement_fits(&canonical, huge_system, &[], budget(Some(15_000)), &prompts,)
            .expect("the rebuild is estimated")
    );
}

#[test]
fn a_line_end_cut_falls_back_to_a_character_boundary() {
    assert_eq!(cut_at_line_end("one\ntwo\nthree", 9), "one\ntwo");
    assert_eq!(cut_at_line_end("one\ntwo\nthree", 100), "one\ntwo\nthree");
    assert_eq!(cut_at_line_end("abcdé", 5), "abcd");
}

#[test]
fn a_compaction_that_may_start_fits_any_summary() {
    let canonical = history("Fix the parser", "also check the tests");
    let prompts = prompts();
    let long_summary = summary_lines(5_000);
    let start = size(&head(system("agent system prompt"), &[], ""));
    // The tightest thresholds that let a compaction start.
    let mut started = 0;
    for token_threshold in start..start + 2_000 {
        if started == 50 {
            break;
        }
        let may_start = minimum_replacement_fits(
            &canonical,
            system("agent system prompt"),
            &[],
            budget(Some(token_threshold)),
            &prompts,
        )
        .expect("the rebuild is estimated");
        if !may_start {
            continue;
        }
        started += 1;
        for summary in [
            Summary::Complete(&long_summary),
            Summary::CutOff(&long_summary),
            Summary::Complete("x"),
        ] {
            assert!(
                rebuild_with(
                    &canonical,
                    system("agent system prompt"),
                    summary,
                    Some(token_threshold)
                )
                .is_some(),
                "threshold {token_threshold}"
            );
        }
    }
    assert_eq!(started, 50, "the tightest thresholds are checked");
}
