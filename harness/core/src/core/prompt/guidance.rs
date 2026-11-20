//! The generic guidance at the start of the system prompt: an intro stance and an autonomy
//! section. Core renders none unless the Runtime chooses its stock text or supplies its own.

use serde::de::Error as _;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::Value;

const STOCK_INTRO: &str = "You help users get complex tasks done. Act directly and proactively.";
const STOCK_AUTHORIZATION: &str = "Do the requested work within the user’s stated scope. A request to prepare or review does not authorize publication, deployment, destructive changes or changes to shared systems. Clarify missing authorization before taking those actions.";
const SKILLS_POINTER: &str = "Find relevant skills with the `skill` tool";
const SKILLS_AUTONOMY_ACTIVITY: &str = "loading skills";
const PROGRAMMATIC_POINTER: &str =
    "discover additional tools with `search_tool_functions`, and call them using `run_typescript`";
const PROGRAMMATIC_AUTONOMY_ACTIVITY: &str = "discovering tools";

/// `settings.prompts.guidance`: `"none"`, `"stock"`, or `{"intro": ..., "autonomy": ...}`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum GuidanceSettings {
    /// No intro stance and no autonomy section.
    None,
    /// Core's stock intro and autonomy section, rebuilt for the Session's features.
    Stock,
    /// Text used as written. Empty text omits that section.
    Custom { intro: String, autonomy: String },
}

impl GuidanceSettings {
    /// The paragraphs this guidance adds before the feature sections. The sentence that says
    /// how to reach skills and searchable tools is feature wiring, so every choice keeps it.
    pub(crate) fn sections(&self, skills: bool, programmatic: bool) -> Vec<String> {
        let pointers = capability_pointers(skills, programmatic);
        let sections = match self {
            Self::None => vec![pointers.unwrap_or_default()],
            Self::Stock => vec![
                stock_intro(skills, programmatic),
                stock_autonomy(skills, programmatic),
            ],
            Self::Custom { intro, autonomy } => {
                // Text saved before the closed choice may carry the stock intro, which already
                // names some pointers; add only the ones it lacks.
                let pointers = capability_pointers(
                    skills && !names(intro, SKILLS_POINTER),
                    programmatic && !names(intro, PROGRAMMATIC_POINTER),
                );
                vec![
                    intro.clone(),
                    pointers.unwrap_or_default(),
                    autonomy.clone(),
                ]
            }
        };
        sections
            .into_iter()
            .filter(|section| !section.trim().is_empty())
            .collect()
    }
}

fn capability_pointers(skills: bool, programmatic: bool) -> Option<String> {
    match (skills, programmatic) {
        (true, true) => Some(format!("{SKILLS_POINTER}, {PROGRAMMATIC_POINTER}.")),
        (true, false) => Some(format!("{SKILLS_POINTER}.")),
        (false, true) => Some(format!("{}.", capitalize(PROGRAMMATIC_POINTER))),
        (false, false) => None,
    }
}

fn names(intro: &str, pointer: &str) -> bool {
    intro.to_lowercase().contains(&pointer.to_lowercase())
}

fn stock_intro(skills: bool, programmatic: bool) -> String {
    match capability_pointers(skills, programmatic) {
        Some(pointers) => format!("{STOCK_INTRO} {pointers}"),
        None => STOCK_INTRO.to_string(),
    }
}

fn stock_autonomy(skills: bool, programmatic: bool) -> String {
    let activities = [
        skills.then_some(SKILLS_AUTONOMY_ACTIVITY),
        programmatic.then_some(PROGRAMMATIC_AUTONOMY_ACTIVITY),
    ]
    .into_iter()
    .flatten()
    .map(|activity| format!("{activity}, "))
    .collect::<String>();
    format!(
        r#"## Autonomy and initiative

- {STOCK_AUTHORIZATION}
- Do not ask for confirmation before {activities}reading or searching available context, or performing other reversible investigation.
- When details are ambiguous, first use the available context and tools to resolve them. Ask a clarifying question only when missing information would materially change the result, cannot be discovered, or additional authorization is genuinely required.
- Make reasonable implementation choices, keep momentum, and continue until the requested outcome is complete or a real blocker remains."#
    )
}

fn capitalize(text: &str) -> String {
    let mut characters = text.chars();
    characters
        .next()
        .map(|first| first.to_uppercase().chain(characters).collect())
        .unwrap_or_default()
}

impl Serialize for GuidanceSettings {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self {
            Self::None => serializer.serialize_str("none"),
            Self::Stock => serializer.serialize_str("stock"),
            Self::Custom { intro, autonomy } => CustomGuidance {
                intro: Some(intro.clone()),
                autonomy: Some(autonomy.clone()),
            }
            .serialize(serializer),
        }
    }
}

impl<'de> Deserialize<'de> for GuidanceSettings {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        match Value::deserialize(deserializer)? {
            Value::String(choice) => match choice.as_str() {
                "none" => Ok(Self::None),
                "stock" => Ok(Self::Stock),
                other => Err(D::Error::custom(format!(
                    "unknown guidance `{other}`, expected `none`, `stock`, or an object with `intro` and `autonomy`"
                ))),
            },
            value @ Value::Object(_) => {
                let custom = CustomGuidance::deserialize(value).map_err(D::Error::custom)?;
                Ok(Self::from_custom(custom))
            }
            _ => Err(D::Error::custom(
                "guidance must be `none`, `stock`, or an object with `intro` and `autonomy`",
            )),
        }
    }
}

impl GuidanceSettings {
    /// Saved settings and overrides from before the closed choice store guidance as text that
    /// was merged over the stock text with every feature, and that stock text meant "rebuild
    /// for the Session's features". Both still read the way they rendered.
    fn from_custom(custom: CustomGuidance) -> Self {
        let intro = custom.intro.unwrap_or_else(|| stock_intro(true, true));
        let autonomy = custom
            .autonomy
            .unwrap_or_else(|| stock_autonomy(true, true));
        if intro == stock_intro(true, true) && is_stock_autonomy(&autonomy) {
            return Self::Stock;
        }
        Self::Custom { intro, autonomy }
    }
}

fn is_stock_autonomy(autonomy: &str) -> bool {
    let stock = stock_autonomy(true, true);
    if autonomy == stock {
        return true;
    }
    // Recognize the exact stock text stored by earlier releases, so it still
    // selects current stock guidance and filters disabled feature references.
    let previous = stock
        .replacen(
            STOCK_AUTHORIZATION,
            "Treat the user's request as authorization to perform the normal, in-scope work needed to complete it. Start working without asking whether to proceed.",
            1,
        )
        .replacen(
            "\n- When details are ambiguous",
            "\n- When a tool call needs approval, make the tool call and let the runtime's approval mechanism handle it. Do not preemptively ask for permission in chat.\n- When details are ambiguous",
            1,
        );
    autonomy == previous
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct CustomGuidance {
    intro: Option<String>,
    autonomy: Option<String>,
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn read(value: Value) -> Result<GuidanceSettings, serde_json::Error> {
        serde_json::from_value(value)
    }

    #[test]
    fn guidance_reads_and_writes_each_choice() {
        let custom = GuidanceSettings::Custom {
            intro: "Intro.".into(),
            autonomy: String::new(),
        };

        assert_eq!(read(json!("none")).unwrap(), GuidanceSettings::None);
        assert_eq!(read(json!("stock")).unwrap(), GuidanceSettings::Stock);
        assert_eq!(
            read(json!({"intro": "Intro.", "autonomy": ""})).unwrap(),
            custom
        );
        for choice in [GuidanceSettings::None, GuidanceSettings::Stock, custom] {
            assert_eq!(
                read(serde_json::to_value(&choice).unwrap()).unwrap(),
                choice
            );
        }
        assert!(read(json!("autonomous")).is_err());
        assert!(read(json!({"intro": "Intro.", "tone": "x"})).is_err());
        assert!(read(json!(null)).is_err());
    }

    /// *Prepare*: Guidance objects as saved before the closed choice: the full stock text,
    /// and partial overrides that named only one field.
    /// *Do*: Read each.
    /// *Assert*: The stock text reads as `stock`; a missing field keeps the stock text.
    #[test]
    fn guidance_saved_as_text_reads_the_way_it_rendered() {
        let intro = stock_intro(true, true);
        let autonomy = stock_autonomy(true, true);

        assert_eq!(
            read(json!({"intro": intro, "autonomy": autonomy})).unwrap(),
            GuidanceSettings::Stock
        );
        assert_eq!(read(json!({})).unwrap(), GuidanceSettings::Stock);
        assert_eq!(
            read(json!({"intro": "Intro."})).unwrap(),
            GuidanceSettings::Custom {
                intro: "Intro.".into(),
                autonomy,
            }
        );
        assert_eq!(
            read(json!({"autonomy": ""})).unwrap(),
            GuidanceSettings::Custom {
                intro,
                autonomy: String::new(),
            }
        );
    }

    #[test]
    fn stock_guidance_names_only_the_features_the_session_has() {
        assert_eq!(
            GuidanceSettings::Stock.sections(false, false),
            [STOCK_INTRO.to_string(), stock_autonomy(false, false)]
        );
        assert_eq!(
            stock_intro(false, true),
            "You help users get complex tasks done. Act directly and proactively. Discover additional tools with `search_tool_functions`, and call them using `run_typescript`."
        );
        assert!(stock_autonomy(true, false).contains("before loading skills, reading"));
    }

    /// *Prepare*: A custom autonomy override saved before the closed choice, so its intro is
    /// the stock intro with the pointers inlined, and a custom intro without them.
    /// *Do*: Render each with every feature.
    /// *Assert*: The pointer sentence appears exactly once in both.
    #[test]
    fn custom_guidance_names_the_capability_pointers_once() {
        let restored = read(json!({"autonomy": "Ask first."})).unwrap();
        let fresh = GuidanceSettings::Custom {
            intro: "Intro.".into(),
            autonomy: "Ask first.".into(),
        };

        for guidance in [restored, fresh] {
            let rendered = guidance.sections(true, true).join("\n\n");
            assert_eq!(rendered.matches(SKILLS_POINTER).count(), 1, "{rendered}");
        }
    }

    /// *Prepare*: A custom intro that names only the `skill` tool.
    /// *Do*: Render it with skills and searchable tools on.
    /// *Assert*: The searchable-tools pointer is still there.
    #[test]
    fn custom_intro_naming_one_pointer_keeps_the_other() {
        let guidance = GuidanceSettings::Custom {
            intro: "Find relevant skills with the `skill` tool.".into(),
            autonomy: String::new(),
        };

        let rendered = guidance.sections(true, true).join("\n\n");

        assert!(rendered.contains("search_tool_functions"), "{rendered}");
    }

    /// *Prepare*: Custom intros equal to the stock intro for one feature.
    /// *Do*: Render each with both features on.
    /// *Assert*: Each names both pointers, each once.
    #[test]
    fn stock_intro_for_one_feature_gets_the_other_pointer() {
        for intro in [stock_intro(true, false), stock_intro(false, true)] {
            let guidance = GuidanceSettings::Custom {
                intro,
                autonomy: String::new(),
            };

            let rendered = guidance.sections(true, true).join("\n\n").to_lowercase();

            assert_eq!(
                rendered.matches(&SKILLS_POINTER.to_lowercase()).count(),
                1,
                "{rendered}"
            );
            assert_eq!(
                rendered
                    .matches(&PROGRAMMATIC_POINTER.to_lowercase())
                    .count(),
                1,
                "{rendered}"
            );
        }
    }

    #[test]
    fn no_guidance_keeps_only_the_capability_pointers() {
        assert!(GuidanceSettings::None.sections(false, false).is_empty());
        assert_eq!(
            GuidanceSettings::None.sections(true, false),
            ["Find relevant skills with the `skill` tool."]
        );
        assert_eq!(
            GuidanceSettings::None.sections(true, true),
            [
                "Find relevant skills with the `skill` tool, discover additional tools with `search_tool_functions`, and call them using `run_typescript`."
            ]
        );
    }
}
