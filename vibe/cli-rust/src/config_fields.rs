//! `/config` field views decoded from the app-server response.

use std::collections::BTreeMap;

use serde_json::Value;

#[derive(Clone)]
pub struct ConfigField {
    pub name: String,
    pub value: String,
    pub popular: bool,
    pub path: String,
    pub raw_value: Value,
    pub kind: String,
    pub writable: bool,
    pub overridden: bool,
    pub enum_choices: Vec<String>,
    pub description: String,
    pub value_labels: BTreeMap<String, String>,
    pub layers: Vec<(String, String)>,
}

pub struct Loaded {
    pub fields: Vec<ConfigField>,
    pub targets: Vec<String>,
}

pub fn parse(result: &Value) -> Loaded {
    let Some(wires) = result.get("fields").and_then(Value::as_array) else {
        return Loaded {
            fields: Vec::new(),
            targets: Vec::new(),
        };
    };
    Loaded {
        fields: wires.iter().map(field).collect(),
        targets: strings(result.get("targets")),
    }
}

fn field(wire: &Value) -> ConfigField {
    let layers = wire.get("layerValues").and_then(Value::as_array);
    ConfigField {
        name: string(wire.get("name")),
        value: format_value(wire.get("value").unwrap_or(&Value::Null)),
        popular: wire
            .get("popular")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        path: string(wire.get("path")),
        raw_value: wire.get("value").cloned().unwrap_or(Value::Null),
        kind: string(wire.get("kind")),
        writable: layers
            .and_then(|items| items.first())
            .and_then(|item| item.get("layer"))
            .and_then(Value::as_str)
            .is_none_or(|layer| layer != "admin"),
        overridden: layers.is_some_and(|items| {
            items
                .iter()
                .any(|item| item.get("layer").and_then(Value::as_str) != Some("default"))
        }),
        enum_choices: strings(wire.get("enumChoices")),
        description: string(wire.get("description")),
        value_labels: wire
            .get("valueLabels")
            .and_then(Value::as_object)
            .map(|labels| {
                labels
                    .iter()
                    .filter_map(|(key, value)| {
                        value.as_str().map(|value| (key.clone(), value.to_owned()))
                    })
                    .collect()
            })
            .unwrap_or_default(),
        layers: layers
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| {
                        Some((
                            item.get("layer")?.as_str()?.to_owned(),
                            format_value(item.get("value").unwrap_or(&Value::Null)),
                        ))
                    })
                    .collect()
            })
            .unwrap_or_default(),
    }
}

fn string(value: Option<&Value>) -> String {
    value.and_then(Value::as_str).unwrap_or_default().to_owned()
}
fn strings(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

pub fn format_value(value: &Value) -> String {
    match value {
        Value::Bool(value) => if *value { "True" } else { "False" }.to_owned(),
        Value::String(value) if value.is_empty() => "\"\"".to_owned(),
        Value::String(value) => value.clone(),
        Value::Array(values) => format!(
            "[{} item{}]",
            values.len(),
            if values.len() == 1 { "" } else { "s" }
        ),
        Value::Object(values) => format!(
            "{{{} entr{}}}",
            values.len(),
            if values.len() == 1 { "y" } else { "ies" }
        ),
        Value::Null => "—".to_owned(),
        Value::Number(value) => value.to_string(),
    }
}
