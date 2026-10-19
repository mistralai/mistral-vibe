//! Welcome banner: the model label and the model-driven cat sprite.

use serde_json::{json, Value};
use vibe_rs::ui::banner::petit_chat::CatVariant;
use vibe_rs::ui::banner::Banner;
use vibe_rs::utils::startup_cache::StartupConfig;

fn runtime(display_name: &str, thinking: &str, extra: Value) -> Value {
    let mut runtime = json!({
        "config": {
            "theme": "ansi-dark",
            "activeModel": {"displayName": display_name, "thinking": thinking},
            "models": [{}, {}],
        },
        "skills": [
            {"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}, {"name": "e"},
            {"name": "builtin", "source": "builtin"},
        ],
    });
    for (key, value) in extra.as_object().unwrap() {
        runtime[key] = value.clone();
    }
    json!({"runtime": runtime})
}

fn config(display_name: &str, thinking: &str, extra: Value) -> StartupConfig {
    StartupConfig::from_runtime("1.0.0", &runtime(display_name, thinking, extra)).unwrap()
}

fn text(banner: &Banner, config: &StartupConfig) -> Vec<String> {
    banner
        .view(config, 120)
        .lines()
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect()
        })
        .collect()
}

#[test]
fn the_model_label_shows_the_display_name_and_thinking_level() {
    let config = config("glm-5.2 (Mistral Hosted)", "max", json!({}));
    assert_eq!(config.active_model, "glm-5.2 (Mistral Hosted)[max]");
    let lines = text(&Banner::default(), &config);
    assert!(
        lines
            .iter()
            .any(|line| line.trim_start().ends_with("glm-5.2 (Mistral Hosted)[max]")),
        "{lines:?}"
    );
}

#[test]
fn large_models_pick_the_chonky_cat() {
    for model in ["Mistral Large 3", "mistral-large-latest", "ml4", "le-chonk"] {
        assert!(
            CatVariant::from_model(model) == CatVariant::LeChonk,
            "{model}"
        );
    }
    for model in ["Medium", "devstral-2", "mistral-small"] {
        assert!(
            CatVariant::from_model(model) == CatVariant::LeChat,
            "{model}"
        );
    }
}

#[test]
fn a_model_switch_swaps_the_cat_sprite() {
    let width = |banner: &Banner| {
        banner
            .chat_frame()
            .lines()
            .map(|row| row.chars().count())
            .max()
            .unwrap_or(0)
    };
    let mut banner = Banner::default();
    let classic = width(&banner);

    banner.sync_model_variant(&config("Mistral Large 3", "off", json!({})));
    let chonk = width(&banner);
    assert!(chonk > classic, "{chonk} > {classic}");

    banner.sync_model_variant(&config("Medium", "off", json!({})));
    assert_eq!(width(&banner), classic);
}
