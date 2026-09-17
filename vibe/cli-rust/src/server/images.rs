//! Prepared image attachments and their session content-block conversion.

use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

use super::types::ContentBlock;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum ImageSource {
    File { path: String },
    Inline { data: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImageAttachment {
    pub source: ImageSource,
    pub alias: String,
    pub mime_type: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PreparedPrompt {
    #[serde(default)]
    pub display_text: String,
    #[serde(default)]
    pub prompt_text: Option<String>,
    #[serde(default, deserialize_with = "deserialize_images")]
    pub images: Vec<ImageAttachment>,
}

impl PreparedPrompt {
    pub fn from_response(response: &Value, fallback: &str) -> Self {
        response
            .get("prompt")
            .cloned()
            .and_then(|value| serde_json::from_value(value).ok())
            .unwrap_or_else(|| Self::from_text(fallback.to_owned()))
    }

    pub fn from_text(text: String) -> Self {
        Self {
            display_text: text.clone(),
            prompt_text: Some(text),
            images: Vec::new(),
        }
    }

    pub fn content_blocks(&self, fallback: &str) -> Vec<ContentBlock> {
        let mut content = Vec::with_capacity(self.images.len() + 1);
        content.push(ContentBlock::Text {
            text: self.prompt_text.as_deref().unwrap_or(fallback).to_owned(),
        });
        content.extend(self.images.iter().map(ImageAttachment::content_block));
        content
    }
}

fn deserialize_images<'de, D>(deserializer: D) -> Result<Vec<ImageAttachment>, D::Error>
where
    D: Deserializer<'de>,
{
    let value = Value::deserialize(deserializer)?;
    Ok(value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| serde_json::from_value(item.clone()).ok())
        .collect())
}

impl ImageAttachment {
    fn content_block(&self) -> ContentBlock {
        let uri = match &self.source {
            ImageSource::File { path } => {
                let resolved =
                    std::fs::canonicalize(path).unwrap_or_else(|_| std::path::PathBuf::from(path));
                url::Url::from_file_path(resolved)
                    .map(|url| url.to_string())
                    .unwrap_or_else(|_| path.clone())
            }
            ImageSource::Inline { data } => format!("data:{};base64,{data}", self.mime_type),
        };
        ContentBlock::Image {
            uri,
            media_type: Some(self.mime_type.clone()),
            alt_text: Some(self.alias.clone()),
        }
    }

    pub fn from_session_block(block: &ContentBlock) -> Option<Self> {
        let ContentBlock::Image {
            uri,
            media_type,
            alt_text,
        } = block
        else {
            return None;
        };
        if let Some(rest) = uri.strip_prefix("data:") {
            let (header, data) = rest.split_once(',')?;
            let mime_type = media_type
                .clone()
                .or_else(|| header.strip_suffix(";base64").map(str::to_owned))?;
            return Some(Self {
                source: ImageSource::Inline {
                    data: data.to_owned(),
                },
                alias: alt_text.clone().unwrap_or_else(|| "image".to_owned()),
                mime_type,
            });
        }
        let path = url::Url::parse(uri)
            .ok()
            .filter(|url| url.scheme() == "file")
            .and_then(|url| url.to_file_path().ok())
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_else(|| uri.clone());
        Some(Self {
            source: ImageSource::File { path: path.clone() },
            alias: alt_text
                .clone()
                .filter(|alias| !alias.is_empty())
                .or_else(|| {
                    std::path::Path::new(&path)
                        .file_name()
                        .and_then(|name| name.to_str())
                        .map(str::to_owned)
                })
                .unwrap_or_else(|| "image".to_owned()),
            mime_type: media_type.clone()?,
        })
    }

    pub fn file_url(&self) -> Option<String> {
        let ImageSource::File { path } = &self.source else {
            return None;
        };
        url::Url::from_file_path(path)
            .ok()
            .map(|url| url.to_string())
    }
}
