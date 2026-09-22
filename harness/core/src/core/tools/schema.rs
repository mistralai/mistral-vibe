use std::borrow::Cow;
use std::marker::PhantomData;

use schemars::generate::{SchemaGenerator, SchemaSettings};
use schemars::{JsonSchema, Schema};
use serde_json::Value;

pub(crate) trait ToolSchema: JsonSchema {
    fn tool_schema() -> Value {
        let settings = SchemaSettings::draft07().with(|settings| {
            settings.inline_subschemas = true;
            settings.meta_schema = None;
        });
        let mut schema = settings
            .into_generator()
            .into_root_schema_for::<Self>()
            .to_value();
        normalize_schema(&mut schema);
        schema
    }
}

impl<T: JsonSchema> ToolSchema for T {}

pub(crate) struct RequiredNullable<T>(PhantomData<T>);

impl<T: JsonSchema> JsonSchema for RequiredNullable<T> {
    fn schema_name() -> Cow<'static, str> {
        <Option<T>>::schema_name()
    }

    fn schema_id() -> Cow<'static, str> {
        <Option<T>>::schema_id()
    }

    fn json_schema(generator: &mut SchemaGenerator) -> Schema {
        <Option<T>>::json_schema(generator)
    }

    fn inline_schema() -> bool {
        true
    }
}

fn normalize_schema(value: &mut Value) {
    match value {
        Value::Array(values) => values.iter_mut().for_each(normalize_schema),
        Value::Object(fields) => {
            fields.values_mut().for_each(normalize_schema);
            fields.remove("title");

            if fields.remove("x-harness-one-of").is_some()
                && let Some(any_of) = fields.remove("anyOf")
            {
                fields.insert("oneOf".to_string(), any_of);
            }
            if fields.contains_key("const") {
                fields.remove("type");
            }
            if matches!(
                fields.get("format").and_then(Value::as_str),
                Some("int32" | "int64" | "uint32" | "uint64" | "float" | "double")
            ) {
                fields.remove("format");
            }
            if fields.remove("x-harness-no-default").is_some() {
                fields.remove("default");
            }
            if fields.remove("x-harness-non-null").is_some() {
                remove_null_type(fields);
            }
            if fields.get("type").and_then(Value::as_str) == Some("object")
                && fields.get("additionalProperties") == Some(&Value::Bool(false))
                && !fields.contains_key("properties")
            {
                fields.insert("properties".to_string(), Value::Object(Default::default()));
            }
        }
        _ => {}
    }
}

fn remove_null_type(fields: &mut serde_json::Map<String, Value>) {
    let Some(Value::Array(types)) = fields.get_mut("type") else {
        return;
    };
    types.retain(|value| value.as_str() != Some("null"));
    if types.len() == 1 {
        let only_type = types.pop().unwrap();
        fields.insert("type".to_string(), only_type);
    }
}
