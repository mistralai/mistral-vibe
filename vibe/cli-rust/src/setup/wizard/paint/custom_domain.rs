//! The custom-domain screen: the domain plus the
//! optional split-horizon browser-auth API base.

use ratatui::layout::Rect;
use ratatui::Frame;

use super::panel::{render, Hint, Row, Subtitle};
use crate::app::App;
use crate::setup::wizard::{InputCard, OnboardingState, ValidationState};

/// Draw the custom domain input screen.
pub(super) fn draw(
    app: &mut App,
    wizard: &mut OnboardingState,
    f: &mut Frame,
    area: Rect,
    chat_frame: &str,
) {
    // An administrator-pinned console renders as one locked card: no API
    // base, no caret, the submit hint under it.
    if wizard.context.pinned_console_url.is_some() {
        let rows = vec![
            Row::Blank(2),
            Row::InputCard {
                input: wizard.custom_domain.clone(),
                focused: false,
                title: "Console URL",
                placeholder: "console.mistral.ai",
                secure: false,
                card: InputCard::Domain,
            },
            Row::Blank(1),
            Row::Feedback(ValidationState::Valid),
            Row::Blank(2),
            Row::Hint(Hint::Keys(&[crate::hints::BACK])),
        ];
        render(
            app,
            wizard,
            f,
            area,
            chat_frame,
            "Sign in to your console",
            Subtitle::Text(
                "Your administrator has configured Vibe to sign in to this console.".into(),
            ),
            &rows,
        );
        return;
    }
    let domain = wizard.custom_domain.clone();
    let api = wizard.custom_domain_api.clone();
    let focus = wizard.custom_domain_focus;
    // Feedback is change-driven: the row describes the last edited
    // input, not the focused one, so Tab never clears it.
    let validation = if wizard.custom_domain_feedback == 0 {
        domain.validation.clone()
    } else {
        api.validation.clone()
    };
    // The first card sits two blank rows under the subtitle — the gap the
    // option screens keep (options.rs). The validation row only exists
    // while it carries text —
    // no content means no reserved padding — and the hint keeps the two
    // blank rows every panel screen uses before it.
    let has_feedback = !matches!(validation, ValidationState::None);
    let mut rows = vec![
        Row::Blank(2),
        Row::InputCard {
            input: domain,
            focused: focus == 0,
            title: "Custom domain",
            placeholder: "console.mistral.ai",
            secure: false,
            card: InputCard::Domain,
        },
        Row::Blank(1),
        Row::InputCard {
            input: api,
            focused: focus == 1,
            // The browser-auth /api sign-in host, NOT the /v1 LLM API base.
            title: "Browser-auth API base (optional)",
            placeholder: "https://connector.internal.example/api",
            secure: false,
            card: InputCard::DomainApi,
        },
    ];
    if has_feedback {
        rows.push(Row::Blank(1));
        rows.push(Row::Feedback(validation));
    }
    rows.push(Row::Blank(2));
    rows.push(Row::Hint(Hint::Keys(&[crate::hints::BACK])));
    render(
        app,
        wizard,
        f,
        area,
        chat_frame,
        "Use a custom domain",
        Subtitle::Text("Point Vibe at a Mistral-compatible deployment.".into()),
        &rows,
    );
}
