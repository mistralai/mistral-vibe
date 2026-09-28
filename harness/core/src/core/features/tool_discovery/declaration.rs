use serde_json::Value;
use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::collections::HashSet;

const MAX_SCHEMA_RENDER_DEPTH: usize = 64;
const VALIDATION_KEYWORDS: [&str; 8] = [
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minProperties",
    "maxProperties",
];

pub(crate) fn to_safe_identifier(name: &str) -> String {
    let mut normalized = name
        .chars()
        .map(|character| {
            if character == '_' || character == '$' || character.is_ascii_alphanumeric() {
                character
            } else {
                '_'
            }
        })
        .collect::<String>();
    if normalized.is_empty() {
        return "_".to_string();
    }
    if !normalized.chars().next().is_some_and(|character| {
        character == '_' || character == '$' || character.is_ascii_alphabetic()
    }) {
        normalized.insert(0, '_');
    }
    normalized
}

pub(crate) fn declaration(
    namespace: &str,
    name: &str,
    description: &str,
    input_schema: &Value,
    output_schema: Option<&Value>,
) -> String {
    let type_name = identifier_to_type_name(name);
    let input_type_name = format!("{type_name}Arg");
    let output_type_name = format!("{type_name}Returned");
    let mut input_renderer = SchemaRenderer::new(input_schema, "Input");
    let input_type = input_renderer.render(input_schema);
    let mut output_renderer = output_schema.map(|schema| SchemaRenderer::new(schema, "Output"));
    let output_type = match (&mut output_renderer, output_schema) {
        (Some(renderer), Some(schema)) => renderer.render(schema),
        _ => "unknown".to_string(),
    };
    let mut declarations = input_renderer.alias_declarations();
    declarations.extend(
        output_renderer
            .into_iter()
            .flat_map(|renderer| renderer.alias_declarations()),
    );
    declarations.push(type_declaration(
        &input_type_name,
        input_schema,
        &input_type,
    ));
    declarations.push(type_declaration(
        &output_type_name,
        output_schema.unwrap_or(&Value::Null),
        &output_type,
    ));
    let mut lines = vec![format!("declare namespace tools.{namespace} {{")];
    lines.extend(
        declarations
            .into_iter()
            .map(|declaration| indent_block(&declaration, 2)),
    );
    if let Some(docstring) = description_jsdoc(description) {
        lines.push(indent_block(&docstring, 2));
    }
    lines.push(format!(
        "  function {name}(arg: {input_type_name}): Promise<{output_type_name}>;"
    ));
    lines.push("}".to_string());
    lines.join("\n")
}

struct AliasDeclaration {
    docstring: Option<String>,
    value: String,
}

struct SchemaRenderer<'a> {
    root: &'a Value,
    alias_prefix: &'static str,
    aliases: BTreeMap<String, AliasDeclaration>,
    reference_aliases: BTreeMap<String, String>,
    used_aliases: BTreeSet<String>,
    failed: bool,
}

impl<'a> SchemaRenderer<'a> {
    fn new(root: &'a Value, alias_prefix: &'static str) -> Self {
        Self {
            root,
            alias_prefix,
            aliases: BTreeMap::new(),
            reference_aliases: BTreeMap::new(),
            used_aliases: BTreeSet::new(),
            failed: false,
        }
    }

    fn render(&mut self, schema: &Value) -> String {
        let rendered = self.render_at_depth(schema, 0);
        if self.failed {
            self.aliases.clear();
            "unknown".to_string()
        } else {
            rendered
        }
    }

    fn render_at_depth(&mut self, schema: &Value, depth: usize) -> String {
        if depth >= MAX_SCHEMA_RENDER_DEPTH {
            self.failed = true;
            return "unknown".to_string();
        }
        let rendered = self.render_non_nullable(schema, depth);
        if schema.get("nullable").and_then(Value::as_bool) == Some(true) {
            union(vec![rendered, "null".to_string()])
        } else {
            rendered
        }
    }

    fn render_non_nullable(&mut self, schema: &Value, depth: usize) -> String {
        if let Some(reference) = schema.get("$ref").and_then(Value::as_str) {
            return self.render_reference(reference, depth);
        }
        if let Some(values) = schema.get("enum").and_then(Value::as_array) {
            return union(values.iter().map(literal).collect());
        }
        if let Some(value) = schema.get("const") {
            return literal(value);
        }
        if let Some(values) = schema.get("allOf").and_then(Value::as_array) {
            return intersection(
                values
                    .iter()
                    .map(|value| self.render_at_depth(value, depth + 1))
                    .collect(),
            );
        }
        for key in ["oneOf", "anyOf"] {
            if let Some(values) = schema.get(key).and_then(Value::as_array) {
                return union(
                    values
                        .iter()
                        .map(|value| self.render_at_depth(value, depth + 1))
                        .collect(),
                );
            }
        }
        if let Some(types) = schema.get("type").and_then(Value::as_array) {
            return union(
                types
                    .iter()
                    .map(|value| {
                        let mut typed_schema = schema.clone();
                        typed_schema["type"] = value.clone();
                        self.render_non_nullable(&typed_schema, depth + 1)
                    })
                    .collect(),
            );
        }
        match schema.get("type").and_then(Value::as_str) {
            Some("string") => "string".to_string(),
            Some("integer" | "number") => "number".to_string(),
            Some("boolean") => "boolean".to_string(),
            Some("null") => "null".to_string(),
            Some("array") => self.render_array(schema, depth),
            Some("object") => self.render_object(schema, depth),
            _ if schema.get("properties").is_some()
                || schema.get("additionalProperties").is_some() =>
            {
                self.render_object(schema, depth)
            }
            _ if schema.get("items").is_some() || schema.get("prefixItems").is_some() => {
                self.render_array(schema, depth)
            }
            _ => "unknown".to_string(),
        }
    }

    fn render_reference(&mut self, reference: &str, depth: usize) -> String {
        let Some(pointer) = reference.strip_prefix('#') else {
            self.failed = true;
            return "unknown".to_string();
        };
        let Some(target) = self.root.pointer(pointer) else {
            self.failed = true;
            return "unknown".to_string();
        };
        if let Some(alias) = self.reference_aliases.get(reference) {
            return alias.clone();
        }
        let alias = self.reserve_reference_alias(reference);
        let value = self.render_at_depth(target, depth + 1);
        self.aliases.insert(
            alias.clone(),
            AliasDeclaration {
                docstring: schema_jsdoc(target),
                value,
            },
        );
        alias
    }

    fn reserve_reference_alias(&mut self, reference: &str) -> String {
        let base = reference_type_name(reference, self.alias_prefix);
        let mut alias = base.clone();
        let mut suffix = 2;
        while !self.used_aliases.insert(alias.clone()) {
            alias = format!("{base}{suffix}");
            suffix += 1;
        }
        self.reference_aliases
            .insert(reference.to_string(), alias.clone());
        alias
    }

    fn render_array(&mut self, schema: &Value, depth: usize) -> String {
        let tuple_items = schema
            .get("prefixItems")
            .or_else(|| schema.get("items").filter(|items| items.is_array()))
            .and_then(Value::as_array);
        if let Some(items) = tuple_items {
            return format!(
                "[{}]",
                items
                    .iter()
                    .map(|item| self.render_at_depth(item, depth + 1))
                    .collect::<Vec<_>>()
                    .join(", ")
            );
        }
        let item = schema
            .get("items")
            .filter(|items| items.is_object())
            .map(|item| self.render_at_depth(item, depth + 1))
            .unwrap_or_else(|| "unknown".to_string());
        format!("Array<{item}>")
    }

    fn render_object(&mut self, schema: &Value, depth: usize) -> String {
        let properties = schema
            .get("properties")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        if properties.is_empty() {
            return match schema.get("additionalProperties") {
                Some(Value::Bool(false)) => "Record<string, never>".to_string(),
                Some(value) if value.is_object() => {
                    format!("Record<string, {}>", self.render_at_depth(value, depth + 1))
                }
                _ => "Record<string, unknown>".to_string(),
            };
        }
        let required = schema
            .get("required")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect::<HashSet<_>>();
        let mut lines = vec!["{".to_string()];
        for (name, value) in properties {
            if let Some(docstring) = schema_jsdoc(&value) {
                lines.push(indent_block(&docstring, 2));
            }
            let optional = if required.contains(name.as_str()) {
                ""
            } else {
                "?"
            };
            let property_type = indent_continuation(&self.render_at_depth(&value, depth + 1), 2);
            lines.push(format!(
                "  {}{}: {property_type};",
                property_name(&name),
                optional,
            ));
        }
        lines.push("}".to_string());
        lines.join("\n")
    }

    fn alias_declarations(self) -> Vec<String> {
        self.aliases
            .into_iter()
            .map(|(name, alias)| {
                let mut lines = alias.docstring.into_iter().collect::<Vec<_>>();
                lines.push(format!("type {name} = {};", alias.value));
                lines.join("\n")
            })
            .collect()
    }
}

pub(crate) fn union(values: Vec<String>) -> String {
    join_types(values, " | ")
}

pub(crate) fn intersection(values: Vec<String>) -> String {
    join_types(values, " & ")
}

fn join_types(values: Vec<String>, separator: &str) -> String {
    let values = values
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    if values.is_empty() {
        "unknown".to_string()
    } else {
        values.join(separator)
    }
}

pub(crate) fn literal(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "unknown".to_string())
}

pub(crate) fn property_name(name: &str) -> String {
    if is_identifier(name) {
        name.to_string()
    } else {
        serde_json::to_string(name).unwrap_or_else(|_| "\"field\"".to_string())
    }
}

pub(crate) fn is_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    chars.next().is_some_and(|character| {
        character == '_' || character == '$' || character.is_ascii_alphabetic()
    }) && chars
        .all(|character| character == '_' || character == '$' || character.is_ascii_alphanumeric())
}

fn reference_type_name(reference: &str, prefix: &str) -> String {
    let name = reference
        .rsplit('/')
        .next()
        .unwrap_or("Value")
        .replace("~1", "/")
        .replace("~0", "~");
    format!(
        "{prefix}{}",
        identifier_to_type_name(&to_safe_identifier(&name))
    )
}

fn identifier_to_type_name(identifier: &str) -> String {
    let pascal = identifier
        .split('_')
        .filter(|word| !word.is_empty())
        .map(|word| {
            let mut characters = word.chars();
            match characters.next() {
                Some(first) => format!("{}{}", first.to_ascii_uppercase(), characters.as_str()),
                None => String::new(),
            }
        })
        .collect::<String>();
    let fallback = if pascal.is_empty() { "_" } else { &pascal };
    if fallback.chars().next().is_some_and(|character| {
        character == '_' || character == '$' || character.is_ascii_alphabetic()
    }) {
        fallback.to_string()
    } else {
        format!("_{fallback}")
    }
}

fn type_declaration(name: &str, schema: &Value, value: &str) -> String {
    let mut lines = schema_jsdoc(schema).into_iter().collect::<Vec<_>>();
    lines.push(format!("type {name} = {value};"));
    lines.join("\n")
}

fn description_jsdoc(description: &str) -> Option<String> {
    render_jsdoc(description.lines().map(escape_jsdoc).collect())
}

fn schema_jsdoc(schema: &Value) -> Option<String> {
    let mut lines = schema
        .get("description")
        .and_then(Value::as_str)
        .into_iter()
        .flat_map(str::lines)
        .map(escape_jsdoc)
        .collect::<Vec<_>>();
    for keyword in VALIDATION_KEYWORDS {
        if let Some(value) = schema.get(keyword).filter(|value| value.is_number()) {
            lines.push(format!("@{keyword} {value}"));
        }
    }
    render_jsdoc(lines)
}

fn render_jsdoc(lines: Vec<String>) -> Option<String> {
    if lines.is_empty() {
        return None;
    }
    Some(
        std::iter::once("/**".to_string())
            .chain(lines.into_iter().map(|line| {
                if line.is_empty() {
                    " *".to_string()
                } else {
                    format!(" * {line}")
                }
            }))
            .chain(std::iter::once(" */".to_string()))
            .collect::<Vec<_>>()
            .join("\n"),
    )
}

fn escape_jsdoc(value: &str) -> String {
    value.replace("*/", "*\\/")
}

fn indent_block(value: &str, spaces: usize) -> String {
    let prefix = " ".repeat(spaces);
    value
        .lines()
        .map(|line| format!("{prefix}{line}"))
        .collect::<Vec<_>>()
        .join("\n")
}

fn indent_continuation(value: &str, spaces: usize) -> String {
    let mut lines = value.lines();
    let Some(first) = lines.next() else {
        return String::new();
    };
    let prefix = " ".repeat(spaces);
    std::iter::once(first.to_string())
        .chain(lines.map(|line| format!("{prefix}{line}")))
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::declaration;

    ///
    /// *Prepare*: A recursive component schema uses legacy `definitions` behind a root `allOf`.
    /// *Do*: Render the tool's TypeScript declaration.
    /// *Assert*: Named input/output types and the function preserve the recursive component union.
    ///
    #[test]
    fn renders_recursive_legacy_definitions_wrapped_in_all_of() {
        // Prepare
        let input_schema = json!({
            "allOf": [{"$ref": "#/definitions/Component"}],
            "definitions": {
                "Component": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {
                                "name": {"const": "Chart"},
                                "props": {
                                    "type": "object",
                                    "properties": {
                                        "children": {
                                            "type": "array",
                                            "items": {"$ref": "#/definitions/Component"}
                                        },
                                        "variant": {"enum": ["line", "bar"]}
                                    },
                                    "required": ["variant"]
                                }
                            },
                            "required": ["name", "props"]
                        },
                        {
                            "type": "object",
                            "properties": {
                                "name": {"const": "Markdown"},
                                "props": {
                                    "type": "object",
                                    "properties": {"content": {"type": "string"}},
                                    "required": ["content"]
                                }
                            },
                            "required": ["name", "props"]
                        }
                    ]
                }
            }
        });

        // Do / Assert
        assert_eq!(
            declaration(
                "ui",
                "renderComponent",
                "Render a component",
                &input_schema,
                Some(&json!({"type": "string"})),
            ),
            r#"declare namespace tools.ui {
  type InputComponent = {
    name: "Chart";
    props: {
      children?: Array<InputComponent>;
      variant: "bar" | "line";
    };
  } | {
    name: "Markdown";
    props: {
      content: string;
    };
  };
  type RenderComponentArg = InputComponent;
  type RenderComponentReturned = string;
  /**
   * Render a component
   */
  function renderComponent(arg: RenderComponentArg): Promise<RenderComponentReturned>;
}"#
        );
    }

    ///
    /// *Prepare*: Two distinct references have the same leaf name.
    /// *Do*: Render the tool declaration.
    /// *Assert*: Each reference gets a stable, distinct alias and keeps its own shape.
    ///
    #[test]
    fn disambiguates_aliases_for_distinct_references() {
        // Prepare
        let input_schema = json!({
            "type": "object",
            "properties": {
                "legacy": {"$ref": "#/definitions/Item"},
                "modern": {"$ref": "#/$defs/Item"}
            },
            "required": ["legacy", "modern"],
            "definitions": {
                "Item": {
                    "type": "object",
                    "properties": {"legacy": {"const": true}},
                    "required": ["legacy"]
                }
            },
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {"modern": {"const": true}},
                    "required": ["modern"]
                }
            }
        });

        // Do / Assert
        assert_eq!(
            declaration(
                "schemas",
                "colliding_refs",
                "Use both item schemas",
                &input_schema,
                None,
            ),
            r#"declare namespace tools.schemas {
  type InputItem = {
    legacy: true;
  };
  type InputItem2 = {
    modern: true;
  };
  type CollidingRefsArg = {
    legacy: InputItem;
    modern: InputItem2;
  };
  type CollidingRefsReturned = unknown;
  /**
   * Use both item schemas
   */
  function colliding_refs(arg: CollidingRefsArg): Promise<CollidingRefsReturned>;
}"#
        );
    }

    ///
    /// *Prepare*: A tool and its properties contain descriptions, a comment terminator, and
    /// JSON Schema validation keywords that TypeScript cannot represent.
    /// *Do*: Render the tool declaration.
    /// *Assert*: The function JSDoc is safe and property JSDoc preserves every validation tag.
    ///
    #[test]
    fn renders_safe_tool_jsdoc_and_validation_tags() {
        // Prepare
        let input_schema = json!({
            "type": "object",
            "properties": {
                "page_size": {
                    "type": "integer",
                    "description": "Maximum number of results",
                    "minimum": 1,
                    "maximum": 25
                },
                "score": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "exclusiveMaximum": 100
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 50
                },
                "config": {
                    "type": "object",
                    "minProperties": 1,
                    "maxProperties": 10
                }
            }
        });

        // Do / Assert
        assert_eq!(
            declaration(
                "search",
                "find_records",
                "Find records before */ returning them.",
                &input_schema,
                None,
            ),
            r#"declare namespace tools.search {
  type FindRecordsArg = {
    /**
     * Maximum number of results
     * @minimum 1
     * @maximum 25
     */
    page_size?: number;
    /**
     * @exclusiveMinimum 0
     * @exclusiveMaximum 100
     */
    score?: number;
    /**
     * @minLength 1
     * @maxLength 50
     */
    name?: string;
    /**
     * @minProperties 1
     * @maxProperties 10
     */
    config?: Record<string, unknown>;
  };
  type FindRecordsReturned = unknown;
  /**
   * Find records before *\/ returning them.
   */
  function find_records(arg: FindRecordsArg): Promise<FindRecordsReturned>;
}"#
        );
    }

    ///
    /// *Prepare*: A tool schema has an unresolved nested reference.
    /// *Do*: Render the tool declaration.
    /// *Assert*: The complete input declaration falls back to a named `unknown` type.
    ///
    #[test]
    fn unresolved_nested_reference_falls_back_to_unknown() {
        // Prepare
        let input_schema = json!({
            "type": "object",
            "$defs": {
                "workflow": {
                    "type": "object",
                    "properties": {
                        "nodes": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/node"}
                        }
                    }
                }
            },
            "properties": {
                "workflow": {"$ref": "#/$defs/workflow"}
            }
        });

        // Do / Assert
        assert_eq!(
            declaration("broken", "broken_tool", "Broken tool", &input_schema, None),
            r#"declare namespace tools.broken {
  type BrokenToolArg = unknown;
  type BrokenToolReturned = unknown;
  /**
   * Broken tool
   */
  function broken_tool(arg: BrokenToolArg): Promise<BrokenToolReturned>;
}"#
        );
    }

    ///
    /// *Prepare*: An array schema represents nullability with a union-valued `type`.
    /// *Do*: Render the tool declaration.
    /// *Assert*: Type-specific schema details survive expansion of the type union.
    ///
    #[test]
    fn union_valued_type_preserves_the_variant_schema() {
        // Prepare
        let input_schema = json!({
            "type": ["array", "null"],
            "items": {"type": "string"}
        });

        // Do / Assert
        assert_eq!(
            declaration("lists", "read_list", "Read a list", &input_schema, None),
            r#"declare namespace tools.lists {
  type ReadListArg = Array<string> | null;
  type ReadListReturned = unknown;
  /**
   * Read a list
   */
  function read_list(arg: ReadListArg): Promise<ReadListReturned>;
}"#
        );
    }
}
