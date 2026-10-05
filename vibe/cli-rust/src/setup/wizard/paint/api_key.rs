//! The API key screen (Python `ApiKeyScreen`): the key-generation link, the
//! masked paste card, and the validation feedback.

use ratatui::layout::Rect;
use ratatui::Frame;

use super::panel::{render, Row, Subtitle};
use crate::app::App;
use crate::setup::wizard::{InputCard, OnboardingState};

/// The configuration docs the "Learn more" footer opens.
const DOCS_LINK: &str =
    "https://github.com/mistralai/mistral-vibe?tab=readme-ov-file#configuration";

/// Draw the API key input screen.
pub(super) fn draw(
    app: &mut App,
    wizard: &mut OnboardingState,
    f: &mut Frame,
    area: Rect,
    chat_frame: &str,
) {
    let input = wizard.api_key_input.clone();
    let provider = &wizard.context.provider;
    let provider_name = title_provider_name(&provider.name);
    // Python `_compose_provider_link`: the key link exists only for the
    // mistral provider and points at the configured vibe base
    // (`{vibe_base_url}/code/extensions?focus=key`); the subtitle names the
    // provider it belongs to.
    let mistral = provider.name == "mistral";
    let help_name = if mistral {
        "Mistral Vibe"
    } else {
        "your provider"
    };
    let provider_link = mistral.then(|| {
        format!(
            "{}/code/extensions?focus=key",
            wizard.context.vibe_base_url.trim_end_matches('/')
        )
    });
    let subtitle = format!("Visit {help_name} to generate or copy your Vibe key");
    // The optional provider link sits two blank rows above the card; without
    // it the card follows the subtitle's single blank row. Unlike the
    // custom-domain screen, the validation row is always reserved (Python's
    // ever-present `#feedback` Static, empty when there is nothing to say),
    // so the layout never shifts when a message appears; the docs rows keep
    // the two blank rows every panel screen uses after the last content row.
    let feedback = Row::Feedback(input.validation.clone());
    let mut rows = vec![Row::Blank(1)];
    if let Some(link) = provider_link {
        rows.push(Row::Link(link));
        rows.push(Row::Blank(2));
    }
    rows.push(Row::InputCard {
        input,
        focused: true,
        title: "Paste API key",
        placeholder: "",
        secure: true,
        card: InputCard::ApiKey,
    });
    rows.push(Row::Blank(1));
    rows.push(feedback);
    rows.push(Row::Blank(2));
    rows.push(Row::Text("Learn more about Vibe configurations".into()));
    rows.push(Row::Link(DOCS_LINK.to_string()));
    render(
        app,
        wizard,
        f,
        area,
        chat_frame,
        &format!("Get your {provider_name} API key"),
        Subtitle::Text(subtitle),
        &rows,
    );
}

/// The provider's display name, first letter uppercased (Python `provider.capitalize`).
fn title_provider_name(name: &str) -> String {
    let mut name = name.to_owned();
    if let Some(first) = name.get_mut(0..1) {
        first.make_ascii_uppercase();
    }
    name
}
