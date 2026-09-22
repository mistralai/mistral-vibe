use serde_json::{Map, Value};

// These limits bound deterministic JSON-to-TypeScript inference while retaining
// representative array elements and object properties.
const MAX_ARRAY_SAMPLING: usize = 15;
const MAX_DEPTH: usize = 8;
const MAX_OBJECT_KEYS: usize = 60;

pub(super) fn infer_typescript_type(value: &Value) -> String {
    let inferred = infer_value_type(value, 0);
    match inferred {
        InferredValueType::Object(object) => {
            format!("export interface Output {}\n", render_object_type(&object))
        }
        inferred => format!("export type Output = {}\n", render_value_type(&inferred)),
    }
}

#[derive(Clone, Debug, PartialEq)]
enum InferredValueType {
    Unknown,
    Null,
    Boolean,
    Integer,
    Number,
    String,
    Array(Option<Box<InferredValueType>>),
    Object(InferredObjectType),
    Union(Vec<InferredValueType>),
}

#[derive(Clone, Debug, PartialEq)]
struct InferredObjectType {
    properties: Vec<InferredProperty>,
    additional_properties: InferredAdditionalProperties,
}

#[derive(Clone, Debug, PartialEq)]
struct InferredProperty {
    name: String,
    value_type: InferredValueType,
    required: bool,
}

#[derive(Clone, Debug, PartialEq)]
enum InferredAdditionalProperties {
    Absent,
    Any,
    Typed(Box<InferredValueType>),
}

fn infer_value_type(value: &Value, depth: usize) -> InferredValueType {
    if depth > MAX_DEPTH {
        return InferredValueType::Unknown;
    }
    match value {
        Value::Null => InferredValueType::Null,
        Value::Bool(_) => InferredValueType::Boolean,
        Value::Number(number) if number_is_integer(number) => InferredValueType::Integer,
        Value::Number(_) => InferredValueType::Number,
        Value::String(_) => InferredValueType::String,
        Value::Array(values) => {
            let mut sampled = values
                .iter()
                .take(MAX_ARRAY_SAMPLING)
                .map(|value| infer_value_type(value, depth + 1));
            let Some(first) = sampled.next() else {
                return InferredValueType::Array(None);
            };
            let items = sampled.fold(first, merge_value_types);
            InferredValueType::Array(Some(Box::new(items)))
        }
        Value::Object(values) => infer_object_type(values, depth),
    }
}

fn number_is_integer(number: &serde_json::Number) -> bool {
    number.is_i64()
        || number.is_u64()
        || number
            .as_f64()
            .is_some_and(|value| value.is_finite() && value.fract() == 0.0)
}

fn infer_object_type(values: &Map<String, Value>, depth: usize) -> InferredValueType {
    if values.is_empty() {
        return InferredValueType::Object(InferredObjectType {
            properties: Vec::new(),
            additional_properties: InferredAdditionalProperties::Absent,
        });
    }

    let properties = values
        .iter()
        .take(MAX_OBJECT_KEYS)
        .map(|(name, value)| InferredProperty {
            name: name.clone(),
            value_type: infer_value_type(value, depth + 1),
            required: true,
        })
        .collect::<Vec<_>>();

    if properties.len() == values.len()
        && let Some(InferredValueType::Object(shared)) = infer_shared_value_type(&properties)
    {
        return InferredValueType::Object(InferredObjectType {
            properties: Vec::new(),
            additional_properties: InferredAdditionalProperties::Typed(Box::new(
                InferredValueType::Object(shared),
            )),
        });
    }

    InferredValueType::Object(InferredObjectType {
        properties,
        additional_properties: if values.len() > MAX_OBJECT_KEYS {
            InferredAdditionalProperties::Any
        } else {
            InferredAdditionalProperties::Absent
        },
    })
}

fn infer_shared_value_type(properties: &[InferredProperty]) -> Option<InferredValueType> {
    if properties.len() < 2 {
        return None;
    }
    let mut shared = properties[0].value_type.clone();
    for property in &properties[1..] {
        shared = merge_value_types(shared, property.value_type.clone());
        if matches!(shared, InferredValueType::Union(_)) {
            return None;
        }
    }
    Some(shared)
}

fn merge_value_types(a: InferredValueType, b: InferredValueType) -> InferredValueType {
    if a == b {
        return a;
    }
    match (a, b) {
        (InferredValueType::Unknown, b) => b,
        (a, InferredValueType::Unknown) => a,
        (InferredValueType::Integer, InferredValueType::Number)
        | (InferredValueType::Number, InferredValueType::Integer) => InferredValueType::Number,
        (InferredValueType::Array(a), InferredValueType::Array(b)) => {
            InferredValueType::Array(match (a, b) {
                (None, None) => None,
                (Some(items), None) | (None, Some(items)) => Some(items),
                (Some(a), Some(b)) => Some(Box::new(merge_value_types(*a, *b))),
            })
        }
        (InferredValueType::Object(a), InferredValueType::Object(b)) => {
            InferredValueType::Object(merge_object_types(&a, &b))
        }
        (InferredValueType::Union(variants), InferredValueType::Union(incoming)) => {
            merge_union_types(variants, incoming)
        }
        (InferredValueType::Union(variants), incoming) => {
            merge_union_types(variants, vec![incoming])
        }
        (first, InferredValueType::Union(incoming)) => merge_union_types(vec![first], incoming),
        (a, b) => InferredValueType::Union(vec![a, b]),
    }
}

fn merge_union_types(
    mut variants: Vec<InferredValueType>,
    incoming: Vec<InferredValueType>,
) -> InferredValueType {
    for schema in incoming {
        if let Some(index) = variants
            .iter()
            .position(|variant| can_merge_by_type(variant, &schema))
        {
            let variant = std::mem::replace(&mut variants[index], InferredValueType::Unknown);
            variants[index] = merge_value_types(variant, schema);
        } else {
            variants.push(schema);
        }
    }
    match variants.as_slice() {
        [single] => single.clone(),
        _ => InferredValueType::Union(variants),
    }
}

fn can_merge_by_type(a: &InferredValueType, b: &InferredValueType) -> bool {
    matches!(
        (a, b),
        (InferredValueType::Null, InferredValueType::Null)
            | (InferredValueType::Boolean, InferredValueType::Boolean)
            | (InferredValueType::Integer, InferredValueType::Integer)
            | (InferredValueType::Number, InferredValueType::Number)
            | (InferredValueType::String, InferredValueType::String)
            | (InferredValueType::Array(_), InferredValueType::Array(_))
            | (InferredValueType::Object(_), InferredValueType::Object(_))
            | (InferredValueType::Integer, InferredValueType::Number)
            | (InferredValueType::Number, InferredValueType::Integer)
    )
}

fn merge_object_types(a: &InferredObjectType, b: &InferredObjectType) -> InferredObjectType {
    let a_additional = additional_properties_type(&a.additional_properties);
    let b_additional = additional_properties_type(&b.additional_properties);
    let mut properties = Vec::new();

    for a_property in &a.properties {
        let b_property = b
            .properties
            .iter()
            .find(|property| property.name == a_property.name);
        let b_type = b_property
            .map(|property| property.value_type.clone())
            .or_else(|| b_additional.cloned());
        properties.push(InferredProperty {
            name: a_property.name.clone(),
            value_type: b_type.map_or_else(
                || a_property.value_type.clone(),
                |b_type| merge_value_types(a_property.value_type.clone(), b_type),
            ),
            required: a_property.required && b_property.is_some_and(|property| property.required),
        });
    }

    for b_property in &b.properties {
        if a.properties
            .iter()
            .any(|property| property.name == b_property.name)
        {
            continue;
        }
        properties.push(InferredProperty {
            name: b_property.name.clone(),
            value_type: a_additional.map_or_else(
                || b_property.value_type.clone(),
                |a_type| merge_value_types(a_type.clone(), b_property.value_type.clone()),
            ),
            required: false,
        });
    }

    InferredObjectType {
        properties,
        additional_properties: merge_additional_properties(
            &a.additional_properties,
            &b.additional_properties,
        ),
    }
}

fn additional_properties_type(
    additional: &InferredAdditionalProperties,
) -> Option<&InferredValueType> {
    match additional {
        InferredAdditionalProperties::Typed(value_type) => Some(value_type),
        InferredAdditionalProperties::Absent | InferredAdditionalProperties::Any => None,
    }
}

fn merge_additional_properties(
    a: &InferredAdditionalProperties,
    b: &InferredAdditionalProperties,
) -> InferredAdditionalProperties {
    match (a, b) {
        (InferredAdditionalProperties::Absent, b) => b.clone(),
        (a, InferredAdditionalProperties::Absent) => a.clone(),
        (InferredAdditionalProperties::Any, _) | (_, InferredAdditionalProperties::Any) => {
            InferredAdditionalProperties::Any
        }
        (InferredAdditionalProperties::Typed(a), InferredAdditionalProperties::Typed(b)) => {
            InferredAdditionalProperties::Typed(Box::new(merge_value_types(
                (**a).clone(),
                (**b).clone(),
            )))
        }
    }
}

fn render_value_type(value_type: &InferredValueType) -> String {
    match value_type {
        InferredValueType::Unknown => "unknown".to_string(),
        InferredValueType::Null => "null".to_string(),
        InferredValueType::Boolean => "boolean".to_string(),
        InferredValueType::Integer | InferredValueType::Number => "number".to_string(),
        InferredValueType::String => "string".to_string(),
        InferredValueType::Array(None) => "unknown[]".to_string(),
        InferredValueType::Array(Some(items)) => {
            let items = match items.as_ref() {
                InferredValueType::Union(_) => format!("({})", render_value_type(items)),
                items => render_value_type(items),
            };
            format!("{items}[]")
        }
        InferredValueType::Object(object) => render_object_type(object),
        InferredValueType::Union(variants) => variants
            .iter()
            .map(render_value_type)
            .collect::<Vec<_>>()
            .join(" | "),
    }
}

fn render_property_type(value_type: &InferredValueType) -> String {
    match value_type {
        InferredValueType::Union(_) => format!("({})", render_value_type(value_type)),
        _ => render_value_type(value_type),
    }
}

fn render_object_type(object: &InferredObjectType) -> String {
    let mut fields = object
        .properties
        .iter()
        .map(|property| {
            format!(
                "{}{}: {}",
                typescript_property_name(&property.name),
                if property.required { "" } else { "?" },
                render_property_type(&property.value_type)
            )
        })
        .collect::<Vec<_>>();
    match &object.additional_properties {
        InferredAdditionalProperties::Absent => {}
        InferredAdditionalProperties::Any => fields.push("[k: string]: unknown".to_string()),
        InferredAdditionalProperties::Typed(value_type) => {
            fields.push(format!("[k: string]: {}", render_property_type(value_type)))
        }
    }
    format!("{{\n{}\n}}", fields.join("\n"))
}

fn typescript_property_name(value: &str) -> String {
    if value == "[k: string]" {
        return value.to_string();
    }
    let mut bytes = value.bytes();
    let valid = bytes
        .next()
        .is_some_and(|byte| byte == b'_' || byte == b'$' || byte.is_ascii_alphabetic())
        && bytes.all(|byte| byte == b'_' || byte == b'$' || byte.is_ascii_alphanumeric());
    if valid {
        value.to_string()
    } else {
        serde_json::to_string(value).expect("property names serialize")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn infers_primitives_and_empty_containers() {
        assert_eq!(
            infer_typescript_type(&Value::Null),
            "export type Output = null\n"
        );
        assert_eq!(
            infer_typescript_type(&json!(true)),
            "export type Output = boolean\n"
        );
        assert_eq!(
            infer_typescript_type(&json!(1)),
            "export type Output = number\n"
        );
        assert_eq!(
            infer_typescript_type(&json!(1.5)),
            "export type Output = number\n"
        );
        assert_eq!(
            infer_typescript_type(&json!("value")),
            "export type Output = string\n"
        );
        assert_eq!(
            infer_typescript_type(&json!([])),
            "export type Output = unknown[]\n"
        );
        assert_eq!(
            infer_typescript_type(&json!({})),
            "export interface Output {\n\n}\n"
        );
    }

    #[test]
    fn merges_sampled_array_element_types() {
        assert_eq!(
            infer_typescript_type(&json!([1, "a", null])),
            "export type Output = (number | string | null)[]\n"
        );
        assert_eq!(
            infer_typescript_type(&json!([[1, 2], ["a"], []])),
            "export type Output = (number | string)[][]\n"
        );
        assert_eq!(
            infer_typescript_type(&json!([{ "v": 1 }, { "v": "x" }])),
            "export type Output = {\nv: (number | string)\n}[]\n"
        );
        assert_eq!(
            infer_typescript_type(&json!([{ "a": 1, "b": 2 }, { "a": 3, "c": 4 }])),
            "export type Output = {\na: number\nb?: number\nc?: number\n}[]\n"
        );
        assert_eq!(
            infer_typescript_type(&json!([{ "a": 1 }, {}])),
            "export type Output = {\na?: number\n}[]\n"
        );
    }

    #[test]
    fn detects_record_like_objects() {
        assert_eq!(
            infer_typescript_type(&json!({
                "first": { "enabled": true, "label": "one" },
                "second": { "enabled": false, "label": "two" },
            })),
            "export interface Output {\n[k: string]: {\nenabled: boolean\nlabel: string\n}\n}\n"
        );
        assert_eq!(
            infer_typescript_type(&json!({
                "first": { "name": "one", "price": 10 },
                "second": { "name": "two" },
                "third": { "name": "three", "stock": 3 },
            })),
            "export interface Output {\n[k: string]: {\nname: string\nprice?: number\nstock?: number\n}\n}\n"
        );
    }

    #[test]
    fn preserves_property_names_and_enforces_the_depth_limit() {
        assert_eq!(
            infer_typescript_type(&json!({
                "$valid": 1,
                "1invalid": 2,
                "also-valid": 3,
                "é": 4,
            })),
            "export interface Output {\n$valid: number\n\"1invalid\": number\n\"also-valid\": number\n\"é\": number\n}\n"
        );
        assert_eq!(
            infer_typescript_type(&json!([{ "[k: string]": 1 }, {}])),
            "export type Output = {\n[k: string]?: number\n}[]\n"
        );
        assert_eq!(
            infer_typescript_type(&json!({
                "a": { "b": { "c": { "d": { "e": { "f": { "g": { "h": { "i": 1 } } } } } } } }
            })),
            "export interface Output {\na: {\nb: {\nc: {\nd: {\ne: {\nf: {\ng: {\nh: {\ni: unknown\n}\n}\n}\n}\n}\n}\n}\n}\n}\n"
        );
    }

    #[test]
    fn enforces_sampling_and_object_key_limits() {
        let mut array = (0..MAX_ARRAY_SAMPLING)
            .map(|value| json!(value))
            .collect::<Vec<_>>();
        array.push(json!("outside sample"));
        assert_eq!(
            infer_typescript_type(&Value::Array(array)),
            "export type Output = number[]\n"
        );

        let mut object = Map::new();
        for index in 0..=MAX_OBJECT_KEYS {
            object.insert(format!("k{index:02}"), json!({ "value": index }));
        }
        let inferred = infer_typescript_type(&Value::Object(object));
        assert!(inferred.contains("k00: {\nvalue: number\n}"));
        assert!(inferred.contains("k59: {\nvalue: number\n}"));
        assert!(!inferred.contains("k60:"));
        assert!(inferred.contains("[k: string]: unknown"));
    }
}
