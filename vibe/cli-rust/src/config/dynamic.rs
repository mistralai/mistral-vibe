//! Client-owned `/config` choices and value labels.

use crate::app::App;
use crate::config_fields::ConfigField;

/// Choices and labels the client owns: the server sends these fields as plain
/// strings because only the client knows the live models and themes.
pub(super) fn apply_choices(app: &App, field: &mut ConfigField) {
    if field.name == "active_model" {
        field.kind = "enum".to_owned();
        field.enum_choices = std::iter::once(String::new())
            .chain(
                app.model_picker
                    .models
                    .iter()
                    .map(|model| model.alias.clone()),
            )
            .collect();
        field.value_labels.insert(
            String::new(),
            format!(
                "default (currently {})",
                app.model_picker.default_display_name
            ),
        );
        for model in &app.model_picker.models {
            field
                .value_labels
                .insert(model.alias.clone(), model.display_name.clone());
        }
    }
    if field.name == "theme" {
        field.kind = "enum".to_owned();
        field.enum_choices = crate::theme_picker::options()
            .into_iter()
            .map(str::to_owned)
            .collect();
    }
}
