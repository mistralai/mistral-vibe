use serde::{Deserialize, Serialize};

/// Whether the Session exposes the file system tools, and what the Runtime's
/// `read_file` returns for files that are not text. Omission keeps the tools
/// enabled with text-only reads, so settings recorded before these fields
/// existed keep their tools and descriptions.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Mode {
    Enabled {
        #[serde(default, skip_serializing_if = "ReadFileSupport::is_text_only")]
        read_file: ReadFileSupport,
    },
    Disabled,
}

impl Default for Mode {
    fn default() -> Self {
        Self::Enabled {
            read_file: ReadFileSupport::default(),
        }
    }
}

impl Mode {
    pub(crate) fn is_enabled(&self) -> bool {
        matches!(self, Self::Enabled { .. })
    }

    /// Whether a serialized document can omit the setting.
    pub(crate) fn is_default(&self) -> bool {
        *self == Self::default()
    }

    pub(crate) fn read_file(&self) -> ReadFileSupport {
        match self {
            Self::Enabled { read_file } => *read_file,
            Self::Disabled => ReadFileSupport::default(),
        }
    }
}

/// What the Runtime's `read_file` returns for files that are not text, for
/// every `file_system.read_file` Action, direct or programmatic.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ReadFileSupport {
    #[serde(default, skip_serializing_if = "ReadFileDocuments::is_unsupported")]
    pub documents: ReadFileDocuments,
    #[serde(default, skip_serializing_if = "ReadFileImages::is_unsupported")]
    pub images: ReadFileImages,
}

impl ReadFileSupport {
    fn is_text_only(&self) -> bool {
        *self == Self::default()
    }
}

/// PDF, Word, PowerPoint, and email files.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ReadFileDocuments {
    #[default]
    Unsupported,
    ExtractedText,
}

impl ReadFileDocuments {
    fn is_unsupported(&self) -> bool {
        matches!(self, Self::Unsupported)
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ReadFileImages {
    #[default]
    Unsupported,
    ExtractedText,
    Native,
}

impl ReadFileImages {
    fn is_unsupported(&self) -> bool {
        matches!(self, Self::Unsupported)
    }
}
