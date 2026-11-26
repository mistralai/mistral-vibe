use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub(crate) struct HookToolKey {
    pub target: HookToolTarget,
    pub qualified_name: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub(crate) enum HookToolTarget {
    #[serde(rename = "self")]
    SelfTool,
    Filesystem,
    Process,
    Provided,
    Skill,
    Subagent,
}
