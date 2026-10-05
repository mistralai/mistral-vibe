//! The custom-domain screen (Python `CustomDomainScreen`): the domain plus the
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
    let domain = wizard.custom_domain.clone();
    let api = wizard.custom_domain_api.clone();
    let focus = wizard.custom_domain_focus;
    // Python's feedback is change-driven: the row describes the last edited
    // input, not the focused one, so Tab never clears it.
    let validation = if wizard.custom_domain_feedback == 0 {
        domain.validation.clone()
    } else {
        api.validation.clone()
    };
    // The first card sits two blank rows under the subtitle — the gap the
    // option screens keep (options.rs). Python's subtitles differ per screen
    // (`margin-bottom: 2` for the option screens, `1` here), and the wizard
    // holds one gap. The validation row only exists while it carries text —
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
    rows.push(Row::Hint(Hint::Press {
        key: "Esc",
        suffix: " to go back",
    }));
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
