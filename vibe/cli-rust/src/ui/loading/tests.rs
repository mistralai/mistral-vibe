use serde_json::json;

use super::SHELL_LOADING_STATUS;
use crate::app::{App, Status};
use crate::approval::{self, Event};
use crate::server::ApprovalCallback;

fn approval(callback_id: &str, status: &str) -> ApprovalCallback {
    serde_json::from_value(json!({
        "callbackId": callback_id,
        "sessionId": "session",
        "detail": {"kind": "approval", "effect": {"display": {"statusText": status}}},
    }))
    .unwrap()
}

fn active_id(app: &App) -> Option<&str> {
    app.approval
        .active
        .as_ref()
        .map(|callback| callback.address.callback_id.as_str())
}

#[test]
fn answering_one_of_two_approvals_keeps_the_loader_paused_on_approval_status() {
    let mut app = App::default();
    app.session.status = Status::Generating {
        since: crate::clock::now(),
    };
    app.view.loading.set_label(SHELL_LOADING_STATUS);
    app.approval
        .enqueue(approval(
            "approval-1",
            "Waiting for approval to run example",
        ))
        .unwrap();
    approval::show_pending(&mut app);
    app.approval
        .enqueue(approval(
            "approval-2",
            "Waiting for approval to run example",
        ))
        .unwrap();
    approval::show_pending(&mut app);
    assert_eq!(active_id(&app), Some("approval-1"));
    assert!(app.approval.has_pending());
    assert_eq!(
        app.view.loading.label(),
        "Waiting for approval to run example"
    );
    let paused_at = app.view.loading.pause_start.expect("the timer is paused");

    approval::apply_event(
        &mut app,
        Event::Responded {
            callback_id: "approval-1".into(),
        },
    );
    assert_eq!(active_id(&app), Some("approval-2"));
    assert!(!app.approval.has_pending());
    assert_eq!(
        app.view.loading.label(),
        "Waiting for approval to run example"
    );
    assert_eq!(app.view.loading.pause_start, Some(paused_at));

    approval::apply_event(
        &mut app,
        Event::Responded {
            callback_id: "approval-2".into(),
        },
    );
    assert!(app.approval.active.is_none());
    assert_eq!(app.view.loading.label(), SHELL_LOADING_STATUS);
    assert!(app.view.loading.pause_start.is_none());
}

#[test]
fn a_long_startup_error_wraps_so_its_closing_hint_is_visible() {
    let mut app = App::default();
    app.session.status = Status::Failed;
    app.session.startup_error = Some(
        "Invalid configuration: Agent 'slow' in /home/user/.vibe/agents/slow.toml: tools: \
         tools.bash: default_timeout is set without max_timeout_seconds. Set both, or neither."
            .into(),
    );
    let mut terminal = ratatui::Terminal::new(ratatui::backend::TestBackend::new(80, 24)).unwrap();
    terminal.draw(|frame| app.draw(frame)).unwrap();
    let buffer = terminal.backend().buffer();
    let screen: String = (0..buffer.area.height)
        .map(|y| {
            (0..buffer.area.width)
                .map(|x| buffer[(x, y)].symbol())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n");
    assert!(screen.contains("Failed to initialize: Invalid configuration"));
    assert!(screen.contains("Set both, or neither."), "{screen}");
}

mod easter_eggs {
    use super::super::easter_eggs::{available, hatch, pick, Mode};
    use super::super::{
        LoadingAnim, DEFAULT_LOADING_STATUS, RETRYING_LOADING_STATUS, SHELL_LOADING_STATUS,
        THINKING_LOADING_STATUS,
    };
    use crate::app::{App, Status};
    use crate::ui::rng::Rng;

    const PLAIN_DAY: (u8, u8) = (5, 14);

    fn roll(label: &str, day: (u8, u8)) -> Vec<Option<&'static str>> {
        let mut rng = Rng::default();
        (0..2_000).map(|_| pick(&mut rng, label, || day)).collect()
    }

    /// An animation with eggs on, re-rolled until `label` shows one.
    fn hatched(anim: &mut LoadingAnim, label: &str) -> &'static str {
        anim.eggs = Mode::Random;
        for _ in 0..1_000 {
            anim.set_label(RETRYING_LOADING_STATUS);
            anim.set_label(label);
            if let Some(egg) = anim.egg {
                return egg;
            }
        }
        panic!("no egg in 1000 rolls");
    }

    #[test]
    fn about_one_generic_label_in_ten_turns_into_an_egg() {
        for label in [DEFAULT_LOADING_STATUS, THINKING_LOADING_STATUS] {
            let eggs = roll(label, PLAIN_DAY).into_iter().flatten().count();
            assert!((100..300).contains(&eggs), "{label}: {eggs} eggs in 2000");
        }
        assert!(roll(DEFAULT_LOADING_STATUS, PLAIN_DAY).contains(&Some("Eating a chocolatine")));
    }

    #[test]
    fn informative_labels_are_never_replaced() {
        assert!(roll(SHELL_LOADING_STATUS, PLAIN_DAY)
            .iter()
            .all(Option::is_none));
        assert!(roll("Waiting for approval to run ls", PLAIN_DAY)
            .iter()
            .all(Option::is_none));
    }

    #[test]
    fn the_date_is_read_only_when_an_egg_hatches() {
        let mut rng = Rng::default();
        let mut reads = 0;
        let mut eggs = 0;
        for label in [SHELL_LOADING_STATUS, DEFAULT_LOADING_STATUS] {
            for _ in 0..2_000 {
                let today = || {
                    reads += 1;
                    PLAIN_DAY
                };
                eggs += usize::from(pick(&mut rng, label, today).is_some());
            }
        }
        assert!(eggs > 0);
        assert_eq!(reads, eggs);
    }

    #[test]
    fn seasonal_eggs_join_on_halloween_and_in_december() {
        assert!(!available(5, 14).contains(&"Carving pumpkins"));
        assert!(!available(10, 30).contains(&"Carving pumpkins"));
        assert!(available(10, 31).contains(&"Carving pumpkins"));
        assert!(!available(11, 30).contains(&"Wrapping presents"));
        assert!(available(12, 1).contains(&"Wrapping presents"));
        assert!(available(12, 1).contains(&"Petting le chat"));
    }

    #[test]
    fn an_egg_stays_for_its_label_and_goes_with_it() {
        let mut anim = LoadingAnim::default();
        let egg = hatched(&mut anim, THINKING_LOADING_STATUS);
        anim.set_label(THINKING_LOADING_STATUS);
        assert_eq!(anim.shown(), egg);
        assert_eq!(anim.label(), THINKING_LOADING_STATUS);
        assert_eq!(anim.total_elements(), egg.chars().count() + 2);

        anim.set_label(RETRYING_LOADING_STATUS);
        assert_eq!(anim.shown(), RETRYING_LOADING_STATUS);
    }

    #[test]
    fn a_blocking_callback_hides_the_egg_and_its_end_rolls_again() {
        let mut anim = LoadingAnim::default();
        hatched(&mut anim, DEFAULT_LOADING_STATUS);
        anim.begin_action_required("Waiting for approval to run ls");
        assert_eq!(anim.shown(), "Waiting for approval to run ls");

        let rolls_an_egg = (0..1_000).any(|_| {
            anim.begin_action_required("Waiting for approval to run ls");
            anim.end_action_required();
            anim.egg.is_some()
        });
        assert!(rolls_an_egg);
        assert_eq!(anim.label(), DEFAULT_LOADING_STATUS);
    }

    #[test]
    fn the_loading_line_shows_the_egg() {
        let mut app = App::default();
        app.session.status = Status::Generating {
            since: crate::clock::now(),
        };
        let egg = hatched(&mut app.view.loading, DEFAULT_LOADING_STATUS);
        let spans = super::super::line::spans(&app, 200).expect("a loading line");
        let text: String = spans.iter().map(|span| span.content.as_ref()).collect();
        assert!(text.contains(&format!("{egg}…")), "{text}");
        assert!(!text.contains(DEFAULT_LOADING_STATUS), "{text}");
    }

    #[test]
    fn a_narrow_line_drops_the_egg_rather_than_wrap_further() {
        let mut app = App::default();
        app.session.status = Status::Generating {
            since: crate::clock::now(),
        };
        let egg = loop {
            let egg = hatched(&mut app.view.loading, DEFAULT_LOADING_STATUS);
            if egg.chars().count() > DEFAULT_LOADING_STATUS.len() + 8 {
                break egg;
            }
        };
        let natural = super::super::width(&app);
        let longer = (egg.chars().count() - DEFAULT_LOADING_STATUS.len()) as u16;
        let drawn = super::super::line::spans(&app, 200).expect("a loading line");
        assert_eq!(super::super::spans_width(&drawn), natural + longer);
        let width = natural + longer - 1;
        let spans = super::super::line::spans(&app, width).expect("a loading line");
        let text: String = spans.iter().map(|span| span.content.as_ref()).collect();
        assert!(
            text.contains(&format!("{DEFAULT_LOADING_STATUS}…")),
            "{text}"
        );
        assert_eq!(super::super::rows(&app, width), 1);
        let wide = super::super::line::spans(&app, natural + longer).expect("a loading line");
        let text: String = wide.iter().map(|span| span.content.as_ref()).collect();
        assert!(text.contains(&format!("{egg}…")), "{text}");
    }

    #[test]
    fn a_forced_egg_replaces_only_the_generic_labels() {
        let mut rng = Rng::default();
        let forced = Mode::Forced("Petting le chat");
        for label in [DEFAULT_LOADING_STATUS, THINKING_LOADING_STATUS] {
            assert_eq!(hatch(forced, &mut rng, label), Some("Petting le chat"));
        }
        assert_eq!(hatch(forced, &mut rng, SHELL_LOADING_STATUS), None);
        assert_eq!(hatch(Mode::Off, &mut rng, DEFAULT_LOADING_STATUS), None);
    }

    #[test]
    fn an_egg_wider_than_a_squeezed_column_gives_way_to_the_label() {
        let mut app = App::default();
        app.session.status = Status::Generating {
            since: crate::clock::now(),
        };
        let egg = loop {
            let egg = hatched(&mut app.view.loading, DEFAULT_LOADING_STATUS);
            if egg.chars().count() > DEFAULT_LOADING_STATUS.len() + 8 {
                break egg;
            }
        };
        let width = 3 + DEFAULT_LOADING_STATUS.len() as u16 + 2;
        let spans = super::super::line::spans(&app, width).expect("a loading line");
        let text: String = spans.iter().map(|span| span.content.as_ref()).collect();
        assert!(!text.contains(egg), "{text}");
        assert!(text.contains(DEFAULT_LOADING_STATUS), "{text}");
    }

    #[test]
    fn the_plain_fallback_keeps_the_wipe_moving_across_its_own_label() {
        let mut app = App::default();
        app.session.status = Status::Generating {
            since: crate::clock::now(),
        };
        let egg = loop {
            let egg = hatched(&mut app.view.loading, DEFAULT_LOADING_STATUS);
            if egg.chars().count() > DEFAULT_LOADING_STATUS.len() + 8 {
                break egg;
            }
        };
        let plain_elements = DEFAULT_LOADING_STATUS.len() + 2;
        assert!(
            app.view.loading.total_elements() > plain_elements + 1,
            "{egg}"
        );
        app.view.loading.progress = plain_elements + 1;
        let width = 3 + DEFAULT_LOADING_STATUS.len() as u16 + 2;
        let spans = super::super::line::spans(&app, width).expect("a loading line");
        let ellipsis = spans
            .iter()
            .find(|span| span.content.starts_with('…'))
            .expect("the ellipsis");
        let current = crate::ui::theme::LOADING_GRADIENT[app.view.loading.color_index];
        assert_eq!(ellipsis.style.fg, Some(current));
    }
}
