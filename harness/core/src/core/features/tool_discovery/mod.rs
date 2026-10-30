#[cfg(feature = "benchmark")]
mod benchmark;
mod declaration;
mod format;

use std::collections::HashMap;
use std::collections::HashSet;
use std::sync::LazyLock;

use crate::core::helpers::schema_error_detail;
use crate::core::step_protocol::{ToolDefinition, ToolDiscoveryKind, ToolDiscoverySummary};
use crate::core::tools::arguments::{
    ArgumentsRejection, SCHEMA_REJECTION_PREFIX, validate_json_schema,
};
use crate::core::tools::result::{
    invalid_tool_call_result, tool_arguments_contract_mismatch_result,
};
use crate::core::tools::schema::ToolSchema;
use crate::core::wire::content::text_content;
use crate::core::wire::tool::{StructuredContent, ToolCall, ToolResult};
pub(crate) use declaration::is_identifier;
use declaration::{declaration, to_safe_identifier};
use format::*;
use jsonschema::error::ValidationErrorKind;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;

pub(crate) const SEARCH_TOOL_FUNCTIONS_NAME: &str = "search_tool_functions";
pub(crate) const MAX_DETAIL_FUNCTION_COUNT: usize = 10;
pub(crate) const MAX_CONNECTOR_COUNT: usize = 10;
pub(crate) const MAX_SUMMARY_RESULT_COUNT: usize = 20;

#[cfg(feature = "benchmark")]
pub(crate) use benchmark::run as run_benchmark;

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, JsonSchema, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SearchMode {
    #[default]
    BestMatch,
    Details,
    AllConnectorCapabilities,
}

impl SearchMode {
    /// The argument this mode needs, as it appears in a call.
    fn input_example(self) -> &'static str {
        match self {
            Self::BestMatch => r#""query": "<keywords>""#,
            Self::Details => r#""functions": ["<connector>.<function>"]"#,
            Self::AllConnectorCapabilities => r#""connectors": ["<connector>"]"#,
        }
    }
}

/// The schema the model sees and the one its arguments are checked against.
static SEARCH_REQUEST_SCHEMA: LazyLock<Value> = LazyLock::new(SearchRequest::tool_schema);

#[derive(Clone, Debug, Default, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(crate) struct SearchRequest {
    #[serde(default)]
    #[schemars(
        description = "best_match returns ranked names; details returns declarations for exact functions; all_connector_capabilities lists exact connector inventories."
    )]
    pub(crate) mode: SearchMode,
    #[schemars(
        description = "Rewrite the needed operation into short tool keywords, e.g. \"list issues\"; do not pass the full user request.",
        extend("x-harness-non-null" = true)
    )]
    pub(crate) query: Option<String>,
    #[serde(default)]
    #[schemars(
        length(max = MAX_DETAIL_FUNCTION_COUNT),
        inner(length(min = 1)),
        description = "Exact {connector}.{function} names for details; required in details mode."
    )]
    pub(crate) functions: Vec<String>,
    #[serde(default, rename = "functionNames")]
    #[schemars(
        length(max = MAX_DETAIL_FUNCTION_COUNT),
        inner(length(min = 1)),
        description = "Deprecated alias for functions.",
        extend("x-harness-no-default" = true)
    )]
    pub(crate) function_names: Vec<String>,
    #[serde(default)]
    #[schemars(
        length(max = MAX_CONNECTOR_COUNT),
        inner(length(min = 1)),
        description = "Exact connector names; required in all_connector_capabilities mode, optional filter in best_match, unused in details."
    )]
    pub(crate) connectors: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ToolDiscoveryResult {
    pub(crate) content: String,
    pub(crate) summary: ToolDiscoverySummary,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ToolDiscoveryExecution {
    pub(crate) result: ToolResult,
    pub(crate) summary: Option<ToolDiscoverySummary>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct ToolDiscovery {
    documents: Vec<SearchDocument>,
}

pub(crate) struct DiscoveryTool<'a> {
    pub(crate) namespace: &'a str,
    pub(crate) name: &'a str,
    pub(crate) description: &'a str,
    pub(crate) input_schema: &'a Value,
    pub(crate) output_schema: Option<&'a Value>,
    pub(crate) integration_description: &'a str,
    pub(crate) connector_icon_url: Option<&'a String>,
}

impl ToolDiscovery {
    pub(crate) fn with_capacity(capacity: usize) -> Self {
        Self {
            documents: Vec::with_capacity(capacity),
        }
    }

    pub(crate) fn add_tool(&mut self, tool: DiscoveryTool<'_>) {
        let group_name = to_safe_identifier(tool.namespace);
        let function_name = to_safe_identifier(tool.name);
        let function_lookup_name = format!("{group_name}.{function_name}");
        let declaration = declaration(
            &group_name,
            &function_name,
            tool.description,
            tool.input_schema,
            tool.output_schema,
        );
        self.documents.push(SearchDocument::ToolFunction {
            integration_name: tool.namespace.to_string(),
            integration_description: tool.integration_description.to_string(),
            connector_icon_url: tool.connector_icon_url.cloned(),
            original_tool_name: tool.name.to_string(),
            function_name,
            tool_reference: format!("tools.{function_lookup_name}"),
            function_lookup_name,
            tool_description: tool.description.to_string(),
            declaration,
        });
    }

    pub(crate) fn add_integration_notice(
        &mut self,
        integration_name: String,
        integration_description: String,
        connector_id: Option<String>,
    ) {
        self.documents.push(SearchDocument::IntegrationNotice {
            integration_name,
            integration_description,
            connector_id,
        });
    }

    #[cfg(test)]
    pub(crate) fn search(&self, request: SearchRequest) -> String {
        search_documents(&self.documents, request).content
    }

    pub(crate) fn search_with_summary(&self, request: SearchRequest) -> ToolDiscoveryResult {
        search_documents(&self.documents, request)
    }
}

pub(crate) fn direct_tool(description: &str) -> ToolDefinition {
    ToolDefinition {
        name: SEARCH_TOOL_FUNCTIONS_NAME.to_string(),
        description: description.to_owned(),
        parameters: SEARCH_REQUEST_SCHEMA.clone(),
    }
}

pub(crate) fn is_direct_name(name: &str) -> bool {
    name == SEARCH_TOOL_FUNCTIONS_NAME
}

pub(crate) fn dispatch(
    call: &ToolCall,
    search: impl FnOnce(SearchRequest) -> ToolDiscoveryResult,
) -> Option<ToolDiscoveryExecution> {
    if !is_direct_name(&call.name) {
        return None;
    }
    let schema = &*SEARCH_REQUEST_SCHEMA;
    if let Err(rejection) = validate_search_arguments(schema, &call.arguments) {
        return Some(ToolDiscoveryExecution {
            result: invalid_tool_call_result(format!(
                "{SCHEMA_REJECTION_PREFIX}{}",
                rejection.detail(schema)
            )),
            summary: None,
        });
    }
    Some(search_tool_message(call, search))
}

/// The schema check every direct tool runs, with this tool's own sentence
/// attached to a schema error when it has one.
fn validate_search_arguments<'a>(
    schema: &Value,
    arguments: &'a Value,
) -> Result<(), ArgumentsRejection<'a>> {
    validate_json_schema(schema, arguments).map_err(|rejection| match rejection {
        ArgumentsRejection::Schema { error, .. } => ArgumentsRejection::Schema {
            explained: explain_schema_error(schema, &error),
            error,
        },
        rejection => rejection,
    })
}

/// Runs a search whose arguments the schema already accepted. A parse
/// failure here means the schema and `SearchRequest` disagree: the call
/// followed the published contract, so the turn goes on and the failure
/// carries its own code instead of the model-mistake one.
fn search_tool_message(
    call: &ToolCall,
    search: impl FnOnce(SearchRequest) -> ToolDiscoveryResult,
) -> ToolDiscoveryExecution {
    let request: SearchRequest = match serde_json::from_value(call.arguments.clone()) {
        Ok(request) => request,
        Err(error) => {
            return ToolDiscoveryExecution {
                result: tool_arguments_contract_mismatch_result(format!(
                    "search_tool_functions arguments match the declared input schema but could not be read: {error}"
                )),
                summary: None,
            };
        }
    };
    let result = search(request);
    ToolDiscoveryExecution {
        result: ToolResult::Success {
            content: text_content(result.content),
            structured_content: StructuredContent::Absent,
            meta: None,
        },
        summary: Some(result.summary),
    }
}

/// Models sometimes send a mode name as an argument name, as in
/// `{"best_match": "list issues"}` or `{"details": {"functions": [...]}}`.
/// The unknown-field error lists the allowed fields but does not say that the
/// rejected field is a value of `mode`, so this adds the call shape for the
/// mode the error names. It names every mode, since a model that sends one
/// mode as an argument name tends to do the same with the next mode it uses.
/// `None` keeps the schema's text.
///
/// Only top-level fields name a mode. The schema has no nested objects today,
/// so the path check only keeps a future nested field from getting this hint.
fn explain_schema_error(schema: &Value, error: &jsonschema::ValidationError<'_>) -> Option<String> {
    if !error.instance_path().as_str().is_empty() {
        return None;
    }
    let ValidationErrorKind::AdditionalProperties { unexpected } = error.kind() else {
        return None;
    };
    let (field, mode) = unexpected.iter().find_map(|field| {
        serde_json::from_value::<SearchMode>(Value::String(field.clone()))
            .ok()
            .map(|mode| (field, mode))
    })?;
    Some(format!(
        r#"{} {} are values of "mode", not argument names. Call search_tool_functions with {{"mode": "{field}", {}}}."#,
        schema_error_detail(schema, error),
        mode_names(schema),
        mode.input_example(),
    ))
}

/// The values of `mode` as the schema lists them, quoted and joined for a
/// sentence: `"a", "b" and "c"`.
fn mode_names(schema: &Value) -> String {
    let names: Vec<String> = schema
        .pointer("/properties/mode/enum")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(|name| format!("\"{name}\""))
        .collect();
    match names.split_last() {
        Some((last, rest)) if !rest.is_empty() => format!("{} and {last}", rest.join(", ")),
        Some((last, _)) => last.clone(),
        None => String::new(),
    }
}

pub(crate) fn search_documents(
    documents: &[SearchDocument],
    request: SearchRequest,
) -> ToolDiscoveryResult {
    match normalize_search_request(request) {
        NormalizedSearchRequest::BestMatch { connectors, query } => {
            search_best_match(documents, &connectors, query.as_deref())
        }
        NormalizedSearchRequest::AllConnectorCapabilities { connectors } => {
            list_connector_capabilities(documents, &connectors)
        }
        NormalizedSearchRequest::Details { functions } => {
            load_tool_function_details(documents, &functions)
        }
    }
}

fn empty_discovery_result(kind: ToolDiscoveryKind, content: String) -> ToolDiscoveryResult {
    ToolDiscoveryResult {
        content,
        summary: discovery_summary(kind, &[]),
    }
}

fn discovery_summary(
    kind: ToolDiscoveryKind,
    documents: &[SearchDocument],
) -> ToolDiscoverySummary {
    let mut tool_count = 0;
    let mut connector_notice_count = 0;
    let mut group_namespaces = Vec::new();
    for document in documents {
        match document {
            SearchDocument::ToolFunction { .. } => tool_count += 1,
            SearchDocument::IntegrationNotice { .. } => connector_notice_count += 1,
        }
        let namespace = document.integration_name();
        if !group_namespaces.iter().any(|group| group == namespace) {
            group_namespaces.push(namespace.to_string());
        }
    }
    ToolDiscoverySummary {
        kind,
        tool_count,
        connector_notice_count,
        group_namespaces,
    }
}

pub(crate) enum NormalizedSearchRequest {
    BestMatch {
        connectors: Vec<String>,
        query: Option<String>,
    },
    AllConnectorCapabilities {
        connectors: Vec<String>,
    },
    Details {
        functions: Vec<String>,
    },
}

pub(crate) fn normalize_search_request(request: SearchRequest) -> NormalizedSearchRequest {
    let functions = request
        .functions
        .into_iter()
        .chain(request.function_names)
        .filter(|name| !name.trim().is_empty())
        .take(MAX_DETAIL_FUNCTION_COUNT)
        .collect::<Vec<_>>();
    let connectors = request
        .connectors
        .into_iter()
        .filter(|name| !name.trim().is_empty())
        .take(MAX_CONNECTOR_COUNT)
        .collect::<Vec<_>>();
    let mode = if request.mode == SearchMode::Details || !functions.is_empty() {
        SearchMode::Details
    } else {
        request.mode
    };
    match mode {
        SearchMode::Details => NormalizedSearchRequest::Details { functions },
        SearchMode::AllConnectorCapabilities => {
            NormalizedSearchRequest::AllConnectorCapabilities { connectors }
        }
        SearchMode::BestMatch => NormalizedSearchRequest::BestMatch {
            connectors,
            query: request.query,
        },
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum SearchDocument {
    ToolFunction {
        integration_name: String,
        integration_description: String,
        connector_icon_url: Option<String>,
        original_tool_name: String,
        function_name: String,
        function_lookup_name: String,
        tool_reference: String,
        tool_description: String,
        declaration: String,
    },
    IntegrationNotice {
        integration_name: String,
        integration_description: String,
        connector_id: Option<String>,
    },
}

impl SearchDocument {
    fn integration_name(&self) -> &str {
        match self {
            Self::ToolFunction {
                integration_name, ..
            }
            | Self::IntegrationNotice {
                integration_name, ..
            } => integration_name,
        }
    }

    fn integration_description(&self) -> &str {
        match self {
            Self::ToolFunction {
                integration_description,
                ..
            }
            | Self::IntegrationNotice {
                integration_description,
                ..
            } => integration_description,
        }
    }

    fn sort_key(&self) -> &str {
        match self {
            Self::ToolFunction {
                function_lookup_name,
                ..
            } => function_lookup_name,
            Self::IntegrationNotice {
                integration_name, ..
            } => integration_name,
        }
    }

    fn key(&self) -> String {
        match self {
            Self::ToolFunction {
                function_lookup_name,
                ..
            } => format!("tool_function:{function_lookup_name}"),
            Self::IntegrationNotice {
                integration_name, ..
            } => format!("integration_notice:{integration_name}"),
        }
    }
}

#[derive(Clone)]
pub(crate) struct ScoredSearchDocument {
    document: SearchDocument,
    score: f64,
}

pub(crate) fn search_best_match(
    documents: &[SearchDocument],
    connectors: &[String],
    query: Option<&str>,
) -> ToolDiscoveryResult {
    if documents.is_empty() {
        return empty_discovery_result(
            ToolDiscoveryKind::BestMatch,
            format_empty_search_result(
                "No tool functions are available in run_typescript.",
                "best_match",
            ),
        );
    }
    let search_query = query.unwrap_or_default().trim();
    if search_query.is_empty() {
        return empty_discovery_result(
            ToolDiscoveryKind::BestMatch,
            format_empty_search_result(
                "No query provided. Describe the capability you need for best_match mode.",
                "best_match",
            ),
        );
    }
    let matching_documents = find_summary_matching_documents(documents, connectors);
    if !connectors.is_empty() && matching_documents.is_empty() {
        return empty_discovery_result(
            ToolDiscoveryKind::BestMatch,
            format_no_summary_results(connectors, ""),
        );
    }
    let mut scored_documents = score_documents(&matching_documents, search_query);
    if !connectors.is_empty() && scored_documents.is_empty() {
        scored_documents = matching_documents
            .iter()
            .filter(|document| matches!(document, SearchDocument::IntegrationNotice { .. }))
            .cloned()
            .map(|document| ScoredSearchDocument {
                document,
                score: 0.0,
            })
            .collect();
    }
    if scored_documents.is_empty() {
        return empty_discovery_result(
            ToolDiscoveryKind::BestMatch,
            format_no_summary_results(connectors, search_query),
        );
    }
    let tool_results = scored_documents
        .iter()
        .filter(|result| matches!(result.document, SearchDocument::ToolFunction { .. }))
        .cloned()
        .collect::<Vec<_>>();
    let notice_results = scored_documents
        .iter()
        .filter(|result| matches!(result.document, SearchDocument::IntegrationNotice { .. }))
        .cloned();
    let related_function_count = tool_results.len();
    let results = tool_results
        .into_iter()
        .take(MAX_SUMMARY_RESULT_COUNT)
        .chain(notice_results)
        .collect::<Vec<_>>();
    let discovered = results
        .iter()
        .map(|result| result.document.clone())
        .collect::<Vec<_>>();
    ToolDiscoveryResult {
        content: format_best_match_results(
            related_function_count > MAX_SUMMARY_RESULT_COUNT,
            related_function_count,
            &results,
        ),
        summary: discovery_summary(ToolDiscoveryKind::BestMatch, &discovered),
    }
}

pub(crate) fn list_connector_capabilities(
    documents: &[SearchDocument],
    connectors: &[String],
) -> ToolDiscoveryResult {
    if documents.is_empty() {
        return empty_discovery_result(
            ToolDiscoveryKind::AllConnectorCapabilities,
            format_empty_search_result(
                "No tool functions are available in run_typescript.",
                "all_connector_capabilities",
            ),
        );
    }
    if connectors.is_empty() {
        return empty_discovery_result(
            ToolDiscoveryKind::AllConnectorCapabilities,
            format_empty_search_result(
                "All_connector_capabilities mode requires at least one exact connector name.",
                "all_connector_capabilities",
            ),
        );
    }
    let matching_documents = find_summary_matching_documents(documents, connectors);
    if matching_documents.is_empty() {
        return empty_discovery_result(
            ToolDiscoveryKind::AllConnectorCapabilities,
            format_empty_search_result(
                &format!(
                    "No exact connectors found for: {}. Try a connector name returned by best_match mode.",
                    connectors.join(", ")
                ),
                "all_connector_capabilities",
            ),
        );
    }
    ToolDiscoveryResult {
        content: format_all_connector_capabilities_results(&matching_documents),
        summary: discovery_summary(
            ToolDiscoveryKind::AllConnectorCapabilities,
            &matching_documents,
        ),
    }
}

pub(crate) fn load_tool_function_details(
    documents: &[SearchDocument],
    functions: &[String],
) -> ToolDiscoveryResult {
    if documents.is_empty() {
        return empty_discovery_result(
            ToolDiscoveryKind::Details,
            format_empty_search_result(
                "No tool functions are available in run_typescript.",
                "details",
            ),
        );
    }
    let mut seen = HashSet::new();
    let mut loaded = Vec::new();
    let mut missing = Vec::new();
    if functions.is_empty() {
        missing.push(None);
    } else {
        for request_name in functions {
            let Some(document) = find_tool_function_document(documents, request_name) else {
                missing.push(Some(request_name.clone()));
                continue;
            };
            let SearchDocument::ToolFunction {
                function_lookup_name,
                ..
            } = document
            else {
                continue;
            };
            if seen.insert(function_lookup_name.clone()) {
                loaded.push(document.clone());
            }
        }
    }
    ToolDiscoveryResult {
        content: format_detail_results(functions.len(), &loaded, &missing),
        summary: discovery_summary(ToolDiscoveryKind::Details, &loaded),
    }
}

pub(crate) fn find_summary_matching_documents(
    documents: &[SearchDocument],
    connectors: &[String],
) -> Vec<SearchDocument> {
    if connectors.is_empty() {
        return documents.to_vec();
    }
    let mut seen = HashSet::new();
    connectors
        .iter()
        .flat_map(|connector| {
            let exact = connector.trim();
            let safe = to_safe_identifier(exact);
            documents.iter().filter(move |document| {
                document.integration_name() == exact
                    || to_safe_identifier(document.integration_name()) == safe
            })
        })
        .filter(|document| seen.insert(document.key()))
        .cloned()
        .collect()
}

pub(crate) fn find_tool_function_document<'a>(
    documents: &'a [SearchDocument],
    request_name: &str,
) -> Option<&'a SearchDocument> {
    let exact = request_name.trim();
    let lookup_name = exact.strip_prefix("tools.").unwrap_or(exact);
    let safe = to_safe_identifier(exact);
    documents.iter().find(|document| match document {
        SearchDocument::ToolFunction {
            original_tool_name,
            function_name,
            function_lookup_name,
            tool_reference,
            ..
        } => {
            function_lookup_name == lookup_name
                || tool_reference == exact
                || original_tool_name == exact
                || function_name == exact
                || function_name == &safe
        }
        SearchDocument::IntegrationNotice { .. } => false,
    })
}

#[derive(Clone)]
pub(crate) struct SearchField {
    text: String,
    weight: f64,
}

pub(crate) fn search_fields(document: &SearchDocument) -> Vec<SearchField> {
    match document {
        SearchDocument::IntegrationNotice {
            integration_name,
            integration_description,
            ..
        } => vec![
            SearchField {
                text: integration_name.clone(),
                weight: 1.0,
            },
            SearchField {
                text: integration_description.clone(),
                weight: 1.0,
            },
        ],
        SearchDocument::ToolFunction {
            integration_name,
            integration_description,
            original_tool_name,
            function_name,
            function_lookup_name,
            tool_reference,
            tool_description,
            declaration,
            ..
        } => vec![
            SearchField {
                text: function_name.clone(),
                weight: 2.0,
            },
            SearchField {
                text: function_lookup_name.clone(),
                weight: 2.0,
            },
            SearchField {
                text: tool_reference.clone(),
                weight: 2.0,
            },
            SearchField {
                text: original_tool_name.clone(),
                weight: 2.0,
            },
            SearchField {
                text: integration_name.clone(),
                weight: 1.0,
            },
            SearchField {
                text: tool_description.clone(),
                weight: 2.0,
            },
            SearchField {
                text: integration_description.clone(),
                weight: 1.0,
            },
            SearchField {
                text: declaration.clone(),
                weight: 1.0,
            },
        ],
    }
}

pub(crate) fn score_documents(
    documents: &[SearchDocument],
    query: &str,
) -> Vec<ScoredSearchDocument> {
    if documents.is_empty() {
        return Vec::new();
    }
    let query_tokens = tokenize_bm25_text(query)
        .into_iter()
        .collect::<BTreeSet<_>>();
    if query_tokens.is_empty() {
        return Vec::new();
    }
    let frequencies = documents
        .iter()
        .map(|document| {
            let mut term_frequencies = HashMap::<String, usize>::new();
            let mut field_weights = HashMap::<String, f64>::new();
            let mut length = 0usize;
            for field in search_fields(document) {
                if field.weight <= 0.0 {
                    continue;
                }
                let tokens = tokenize_bm25_text(&field.text);
                length += tokens.len();
                for token in tokens {
                    if !query_tokens.contains(&token) {
                        continue;
                    }
                    *term_frequencies.entry(token.clone()).or_default() += 1;
                    field_weights
                        .entry(token)
                        .and_modify(|weight| *weight = weight.max(field.weight))
                        .or_insert(field.weight);
                }
            }
            (length, term_frequencies, field_weights)
        })
        .collect::<Vec<_>>();
    let average_length = frequencies
        .iter()
        .map(|(length, _, _)| *length as f64)
        .sum::<f64>()
        / documents.len() as f64;
    let document_frequencies = query_tokens
        .iter()
        .map(|token| {
            let count = frequencies
                .iter()
                .filter(|(_, terms, _)| terms.contains_key(token))
                .count();
            (token.as_str(), count as f64)
        })
        .collect::<HashMap<_, _>>();
    let mut results = documents
        .iter()
        .enumerate()
        .filter_map(|(index, document)| {
            let (length, term_frequencies, field_weights) = &frequencies[index];
            let length_ratio = if average_length == 0.0 {
                0.0
            } else {
                *length as f64 / average_length
            };
            let length_normalization = 1.2 * (1.0 - 0.75 + 0.75 * length_ratio);
            let score = query_tokens
                .iter()
                .filter_map(|token| {
                    let term_frequency = *term_frequencies.get(token)? as f64;
                    let document_frequency = document_frequencies[token.as_str()];
                    let idf = (1.0
                        + (documents.len() as f64 - document_frequency + 0.5)
                            / (document_frequency + 0.5))
                        .ln();
                    let field_weight = field_weights.get(token).copied().unwrap_or(1.0);
                    Some(
                        idf * field_weight * (term_frequency * 2.2)
                            / (term_frequency + length_normalization),
                    )
                })
                .sum::<f64>();
            (score > 0.0).then(|| ScoredSearchDocument {
                document: document.clone(),
                score,
            })
        })
        .collect::<Vec<_>>();
    results.sort_by(|left, right| {
        right
            .score
            .partial_cmp(&left.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.document.sort_key().cmp(right.document.sort_key()))
    });
    results
}

pub(crate) fn tokenize_bm25_text(text: &str) -> Vec<String> {
    let characters = text.chars().collect::<Vec<_>>();
    let mut expanded = String::with_capacity(text.len());
    for (index, character) in characters.iter().copied().enumerate() {
        let previous = index
            .checked_sub(1)
            .and_then(|value| characters.get(value))
            .copied();
        let next = characters.get(index + 1).copied();
        let boundary = character.is_ascii_uppercase()
            && (previous.is_some_and(|value| value.is_ascii_lowercase() || value.is_ascii_digit())
                || (previous.is_some_and(|value| value.is_ascii_uppercase())
                    && next.is_some_and(|value| value.is_ascii_lowercase())));
        if boundary {
            expanded.push(' ');
        }
        expanded.push(character.to_ascii_lowercase());
    }
    expanded
        .split(|character: char| !character.is_ascii_alphanumeric())
        .filter(|token| !token.is_empty())
        .map(ToString::to_string)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::tools::resolved::testing::*;

    /// Arguments the schema accepts must parse, or a valid call would fail
    /// as a Core invariant instead of running.
    #[test]
    fn arguments_the_schema_accepts_parse_as_a_search_request() {
        let modes = [
            SearchMode::BestMatch,
            SearchMode::Details,
            SearchMode::AllConnectorCapabilities,
        ];
        let mut calls: Vec<Value> = modes
            .into_iter()
            .map(|mode| {
                let mode_name = serde_json::to_value(mode).unwrap();
                serde_json::from_str(&format!(
                    "{{\"mode\": {mode_name}, {}}}",
                    mode.input_example()
                ))
                .unwrap()
            })
            .collect();
        calls.push(serde_json::json!({
            "mode": "details",
            "query": "q",
            "functions": vec!["a.b"; MAX_DETAIL_FUNCTION_COUNT],
            "functionNames": ["a.c"],
            "connectors": vec!["a"; MAX_CONNECTOR_COUNT],
        }));

        for call in calls {
            assert!(
                validate_search_arguments(&SEARCH_REQUEST_SCHEMA, &call).is_ok(),
                "schema rejects {call}"
            );
            assert!(
                serde_json::from_value::<SearchRequest>(call.clone()).is_ok(),
                "serde rejects {call}"
            );
        }
    }

    /// The schema and `SearchRequest` agree today, so this calls the parse
    /// step directly with arguments `SearchRequest` cannot read, as it would
    /// see them if the two drifted apart.
    #[test]
    fn arguments_the_schema_accepts_but_cannot_be_read_fail_as_a_contract_mismatch() {
        let call = ToolCall {
            id: "drifted".to_string(),
            name: SEARCH_TOOL_FUNCTIONS_NAME.to_string(),
            arguments: serde_json::json!({"mode": "best_match", "query": 7}),
            argument_error: None,
        };

        let execution = search_tool_message(&call, |_| {
            panic!("a call that cannot be read must not search")
        });

        let ToolResult::Failure { content, error, .. } = execution.result else {
            panic!("expected a failure");
        };
        assert_eq!(error.code, "tool_arguments_contract_mismatch");
        assert!(error.message.starts_with(
            "search_tool_functions arguments match the declared input schema but could not be read: "
        ));
        assert!(
            serde_json::to_string(&content)
                .unwrap()
                .contains("InvalidToolCall")
        );
        assert!(execution.summary.is_none());
    }

    #[test]
    fn best_match_uses_bm25_ranking() {
        let groups = vec![
            programmatic_group(
                "slack",
                "Slack collaboration tools",
                [(
                    "message_search",
                    "Search messages across Slack channels".to_string(),
                )],
            ),
            programmatic_group(
                "github",
                "GitHub repository tools",
                [(
                    "code_search",
                    "Search source code in repositories".to_string(),
                )],
            ),
        ];

        let mut config = config();
        config.settings.tools.subagents = SubagentMode::Disabled;
        config.capabilities.tool_groups = groups;
        let result = search(
            &config,
            SearchRequest {
                query: Some("slack message search".to_string()),
                ..SearchRequest::default()
            },
        );

        let slack = result.find("1. slack.message_search").unwrap();
        let github = result.find("github.code_search");
        assert!(github.is_none_or(|github| slack < github));
        assert!(!result.contains("declare namespace"));
    }

    #[test]
    fn best_match_caps_callable_results_without_losing_connector_guidance() {
        let mut groups = vec![programmatic_group(
            "search helpers",
            "slotcapunique search helper tools",
            (1..=22).map(|index| {
                (
                    format!("search_helper_{index}"),
                    format!("slotcapunique helper tool {index}"),
                )
            }),
        )];
        groups.push(connector_notice(
            "gmail",
            "gmail slotcapunique connector is not authenticated yet.",
            "gmail-id",
        ));

        let mut config = config();
        config.settings.tools.subagents = SubagentMode::Disabled;
        config.capabilities.tool_groups = groups;
        let result = search(
            &config,
            SearchRequest {
                query: Some("gmail slotcapunique".to_string()),
                ..SearchRequest::default()
            },
        );

        assert_eq!(
            result
                .lines()
                .filter(|line| {
                    line.split_once(". ")
                        .is_some_and(|(index, _)| index.parse::<usize>().is_ok())
                })
                .count(),
            20
        );
        assert!(result.contains("relatedFunctionCount: 22"));
        assert!(result.contains("moreCandidatesAvailable: true"));
        assert!(result.contains("<name>gmail</name>"));
        assert!(
            result.contains("<connector-id>gmail-id</connector-id>"),
            "{result}"
        );
    }

    #[test]
    fn all_connector_capabilities_returns_the_full_sorted_inventory() {
        let groups = vec![programmatic_group(
            "slack",
            "Slack collaboration tools",
            (1..=22).map(|index| {
                (
                    format!("channel_action_{index}"),
                    format!("connectorlimitunique Slack action {index}"),
                )
            }),
        )];

        let mut config = config();
        config.settings.tools.subagents = SubagentMode::Disabled;
        config.capabilities.tool_groups = groups;
        let result = search(
            &config,
            SearchRequest {
                mode: SearchMode::AllConnectorCapabilities,
                connectors: vec!["slack".to_string()],
                ..SearchRequest::default()
            },
        );
        let names = result
            .lines()
            .filter_map(|line| {
                let (index, name) = line.split_once(". ")?;
                index.parse::<usize>().ok().map(|_| name)
            })
            .collect::<Vec<_>>();

        assert_eq!(names.len(), 22);
        assert_eq!(names.first(), Some(&"slack.channel_action_1"));
        assert_eq!(names.get(1), Some(&"slack.channel_action_10"));
        assert_eq!(names.last(), Some(&"slack.channel_action_9"));
        assert!(!result.contains("connectorlimitunique Slack action"));
    }

    #[test]
    fn details_preserve_requested_order_and_deduplicate_exact_aliases() {
        let groups = vec![
            programmatic_group(
                "slack",
                "Slack collaboration tools",
                [
                    ("search_messages", "Search Slack messages".to_string()),
                    ("search_threads", "Search Slack threads".to_string()),
                ],
            ),
            programmatic_group(
                "notion",
                "Notion workspace tools",
                [("search_pages", "Search Notion pages".to_string())],
            ),
        ];

        let mut config = config();
        config.settings.tools.subagents = SubagentMode::Disabled;
        config.capabilities.tool_groups = groups;
        let result = search(
            &config,
            SearchRequest {
                mode: SearchMode::Details,
                functions: vec![
                    "notion.search_pages".to_string(),
                    "search_threads".to_string(),
                    "tools.slack.search_messages".to_string(),
                    "slack.search_threads".to_string(),
                    "missing.function".to_string(),
                ],
                ..SearchRequest::default()
            },
        );

        let notion = result.find("function search_pages").unwrap();
        let threads = result.find("function search_threads").unwrap();
        let messages = result.find("function search_messages").unwrap();
        assert!(notion < threads && threads < messages);
        assert_eq!(result.matches("function search_threads").count(), 1);
        assert!(result.contains("requestedFunctionCount: 5"));
        assert!(result.contains("loadedFunctionCount: 3"));
        assert!(result.contains("- No exact tool function found for: missing.function"));
    }
}
