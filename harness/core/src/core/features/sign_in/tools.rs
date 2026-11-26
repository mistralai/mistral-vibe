#![expect(
    dead_code,
    reason = "private contract types are reflected by Schemars rather than constructed"
)]

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde_json::Value;

use crate::core::config::HarnessConfig;
use crate::core::step_protocol::ToolDefinition;
use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::tools::schema::ToolSchema;

/// The stable ID of the tool that asks the user to sign in.
pub(crate) const REQUEST_TOOL_ID: &str = "builtin.auth.request_sign_in";
/// The stable ID of the tool that lists the sources the user has not signed in to.
pub(crate) const LIST_TOOL_ID: &str = "builtin.auth.list_unauthenticated_sources";

/// The request tool's description, naming where the model finds source ids.
macro_rules! request_description {
    ($ids_from:literal) => {
        concat!(
            "Ask the user to sign in to sources you need, such as connectors. ",
            "This call is itself the prompt the user answers: do not ask them first. ",
            "It waits for their answer. Pass each source's type and id, as ",
            $ids_from,
            " gives them. A source that is already signed in ",
            "can be signed in to again: do that only after its tools or commands failed because it ",
            "is signed out or its credentials expired. The result lists the sources connected, with ",
            "what each gives (its tools, credentials your commands use, or both), denied, ",
            "or not connected."
        )
    };
}

/// For a Session that also offers `list_unauthenticated_sources`.
const REQUEST_DESCRIPTION_WITH_LIST: &str =
    request_description!("list_unauthenticated_sources or a notice");
/// For a Session without `list_unauthenticated_sources`, which the model then never sees.
const REQUEST_DESCRIPTION: &str = request_description!("a notice");

const LIST_DESCRIPTION: &str = concat!(
    "List the sources the user has not signed in to, with the type and id ",
    "request_sign_in takes. Pass query to keep only the sources whose name contains it. ",
    "Sources that are already connected are never in this list, and neither are sources ",
    "that cannot be connected, so a source missing from it is not necessarily connected: ",
    "this tool cannot tell you which sources are connected."
);

/// The kind of source a sign-in is for. MCP servers come later.
// Variants carry no doc comments, so the schema is a plain string `enum`.
#[derive(JsonSchema)]
#[serde(rename_all = "snake_case")]
enum SourceType {
    Connector,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct SourceReference {
    /// The kind of source.
    r#type: SourceType,
    /// The source's id.
    #[schemars(length(min = 1))]
    id: String,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct RequestArguments {
    /// The sources to sign in to.
    #[schemars(length(min = 1))]
    sources: Vec<SourceReference>,
}

/// What a connected source gives.
#[derive(JsonSchema)]
#[serde(rename_all = "snake_case")]
enum Access {
    Tools,
    Credentials,
}

#[derive(JsonSchema)]
struct ConnectedSource {
    /// The kind of source.
    r#type: SourceType,
    /// The source's id.
    id: String,
    /// What the source gives now: its tools, credentials the commands use, or both.
    #[schemars(length(min = 1))]
    access: Vec<Access>,
}

/// Why a source is not connected.
#[derive(JsonSchema)]
#[serde(rename_all = "snake_case")]
enum NotConnectedReason {
    /// The user did not answer in time.
    Timeout,
    /// The sign-in ended without an answer.
    Closed,
    /// The user answered, but the source is still signed out.
    StillSignedOut,
}

#[derive(JsonSchema)]
struct NotConnectedSource {
    /// The kind of source.
    r#type: SourceType,
    /// The source's id.
    id: String,
    /// timeout: the user did not answer in time. closed: the sign-in ended without an
    /// answer. still_signed_out: the user answered, but the source is still signed out.
    reason: NotConnectedReason,
}

#[derive(JsonSchema)]
struct UnverifiedSource {
    /// The kind of source.
    r#type: SourceType,
    /// The source's id.
    id: String,
    /// The accesses whose status could not be checked.
    #[schemars(length(min = 1))]
    access: Vec<Access>,
    /// Whether the status is checked again at the start of a later turn.
    retry_after_turn: bool,
}

/// What the check of one access found: confirmed, it works now; known_negative, it is
/// still signed out; unverified, its status could not be checked.
// Variants carry no doc comments, so the schema is a plain string `enum`.
#[derive(JsonSchema)]
#[serde(rename_all = "snake_case")]
enum AccessOutcome {
    Confirmed,
    KnownNegative,
    Unverified,
}

#[derive(JsonSchema)]
struct AccessOutcomes {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    tools: Option<AccessOutcome>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    credentials: Option<AccessOutcome>,
}

/// Each requested source lands in connected, denied or not_connected, except a source
/// none of whose accesses connected while one could not be checked: unverified alone
/// accounts for it. A source in another list can also be in unverified for an access
/// that could not be checked.
#[derive(JsonSchema)]
struct RequestResult {
    /// The sources the user connected, with only the accesses that work now.
    connected: Vec<ConnectedSource>,
    /// The sources the user refused to connect.
    denied: Vec<SourceReference>,
    /// The sources that are not connected, with the reason.
    not_connected: Vec<NotConnectedSource>,
    /// The accesses whose status could not be checked, by source.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    unverified: Option<Vec<UnverifiedSource>>,
    /// What the check found for each access of a source that is still signed out for one
    /// access while another connected or could not be checked, by source id.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    access_outcomes: Option<BTreeMap<String, AccessOutcomes>>,
    /// What the user or the app asks the agent to do next, if anything.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    next_steps: Option<String>,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ListArguments {
    /// Keep only the sources whose name contains this text, ignoring case.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    query: Option<String>,
}

#[derive(JsonSchema)]
struct ListedSource {
    /// The kind of source.
    r#type: SourceType,
    /// The source's id.
    id: String,
    /// The source's display name.
    name: String,
}

#[derive(JsonSchema)]
struct ListResult {
    /// The sources the user has not signed in to.
    sources: Vec<ListedSource>,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum ToolName {
    Request,
    List,
}

impl ToolName {
    pub(crate) const ALL: [Self; 2] = [Self::List, Self::Request];

    /// The name the model calls the tool by.
    pub(crate) fn direct_name(self) -> &'static str {
        match self {
            Self::Request => "request_sign_in",
            Self::List => "list_unauthenticated_sources",
        }
    }

    pub(crate) fn runtime_tool(self) -> RuntimeBuiltinToolName {
        match self {
            Self::Request => RuntimeBuiltinToolName::SignInRequest,
            Self::List => RuntimeBuiltinToolName::SignInList,
        }
    }

    fn description(self, lists_sources: bool) -> &'static str {
        match self {
            Self::Request if lists_sources => REQUEST_DESCRIPTION_WITH_LIST,
            Self::Request => REQUEST_DESCRIPTION,
            Self::List => LIST_DESCRIPTION,
        }
    }

    fn input_schema(self) -> Value {
        match self {
            Self::Request => RequestArguments::tool_schema(),
            Self::List => ListArguments::tool_schema(),
        }
    }

    fn output_schema(self) -> Value {
        match self {
            Self::Request => RequestResult::tool_schema(),
            Self::List => ListResult::tool_schema(),
        }
    }
}

pub(crate) struct RuntimeContract {
    pub(crate) name: RuntimeBuiltinToolName,
    pub(crate) input_schema: Value,
    pub(crate) output_schema: Value,
}

/// The input and output contracts of every sign-in tool, offered or not, so a
/// persisted call settles after the Runtime stopped offering the tools.
pub(crate) fn runtime_contracts() -> Vec<RuntimeContract> {
    ToolName::ALL
        .into_iter()
        .map(|name| RuntimeContract {
            name: name.runtime_tool(),
            input_schema: name.input_schema(),
            output_schema: name.output_schema(),
        })
        .collect()
}

/// The sign-in tools `config` offers: none without `sign_in`; the list only with
/// `list_unauthenticated_sources` too.
pub(crate) fn offered(config: &HarnessConfig) -> Vec<ToolName> {
    if !config.offers_sign_in() {
        return Vec::new();
    }
    ToolName::ALL
        .into_iter()
        .filter(|name| *name == ToolName::Request || config.offers_sign_in_list())
        .collect()
}

/// The sign-in tools `config` offers, as top-level model tools.
pub(crate) fn direct_tools(config: &HarnessConfig) -> Vec<ToolDefinition> {
    offered(config)
        .into_iter()
        .map(|name| ToolDefinition {
            name: name.direct_name().to_string(),
            description: name.description(config.offers_sign_in_list()).to_string(),
            parameters: name.input_schema(),
        })
        .collect()
}

pub(crate) fn is_direct_name(name: &str) -> bool {
    ToolName::ALL
        .into_iter()
        .any(|tool| tool.direct_name() == name)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn request_schema_takes_typed_sources() {
        let schema = ToolName::Request.input_schema();

        assert_eq!(schema["required"], json!(["sources"]));
        assert_eq!(schema["additionalProperties"], json!(false));
        let source = &schema["properties"]["sources"]["items"];
        assert_eq!(source["properties"]["type"]["enum"], json!(["connector"]));
        assert_eq!(schema["properties"]["sources"]["minItems"], json!(1));
    }

    #[test]
    fn request_result_accounts_for_unverified_access_as_optional_diagnostics() {
        let schema = ToolName::Request.output_schema();

        assert_eq!(
            schema["required"],
            json!(["connected", "denied", "not_connected"])
        );
        let unverified = &schema["properties"]["unverified"]["items"];
        assert_eq!(
            unverified["required"],
            json!(["type", "id", "access", "retry_after_turn"])
        );
        let outcomes = &schema["properties"]["access_outcomes"]["additionalProperties"];
        assert_eq!(
            outcomes["properties"]["credentials"]["enum"],
            json!(["confirmed", "known_negative", "unverified", null])
        );
    }

    #[test]
    fn stable_ids_name_the_namespace_and_direct_names() {
        for tool in ToolName::ALL {
            assert_eq!(
                tool.runtime_tool().as_str(),
                format!("builtin.auth.{}", tool.direct_name())
            );
        }
        assert_eq!(ToolName::Request.runtime_tool().as_str(), REQUEST_TOOL_ID);
        assert_eq!(ToolName::List.runtime_tool().as_str(), LIST_TOOL_ID);
    }
}
