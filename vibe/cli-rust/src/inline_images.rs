//! Open an inline image attachment, such as a resumed one, through a temporary file.

use std::io::Write as _;
use std::path::PathBuf;

use base64::Engine as _;
use serde_json::Value;

use crate::app::App;
use crate::server::{ImageAttachment, ImageSource};

const PREFIX: &str = "vibe-image-";

/// Link target naming image `index` of transcript entry `entry_id`.
pub fn target(entry_id: &str, index: usize) -> String {
    format!("{entry_id}#{index}")
}

/// Write the linked inline image to a temporary file and open it.
pub fn open(app: &App, target: &str) {
    let Some(path) = materialize(app, target) else {
        tracing::warn!(target, "inline image is no longer in the transcript");
        return;
    };
    match url::Url::from_file_path(&path) {
        Ok(url) => crate::external_url::open_file(url.as_str()),
        Err(()) => tracing::warn!(?path, "inline image path is not absolute"),
    }
}

fn materialize(app: &App, target: &str) -> Option<PathBuf> {
    let (entry_id, index) = target.rsplit_once('#')?;
    let raw = app.view.transcript.entry_raw(entry_id)?;
    let attachment = attachments(raw).into_iter().nth(index.parse().ok()?)?;
    match attachment.source {
        ImageSource::Inline { data } => inline_file(&data, &attachment.mime_type),
        ImageSource::File { .. } => None,
    }
}

/// The image attachments of a transcript entry, in content order.
pub fn attachments(raw: &Value) -> Vec<ImageAttachment> {
    raw.get("content")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|block| block.get("type").and_then(Value::as_str) == Some("image"))
        .filter_map(|block| serde_json::from_value(block.get("attachment")?.clone()).ok())
        .collect()
}

/// Give a rewound prompt back the images its `[Image #N]` placeholders name,
/// so resending it attaches them again even in a process that never pasted
/// them (a resumed session), and mark the placeholders as mentions. An image
/// keeps the placeholder it is named after; the rest pair up in order.
pub fn restore_placeholders(app: &mut App, text: &str, images: &[ImageAttachment]) {
    let mut labels: Vec<&str> = Vec::new();
    for label in crate::image_placeholders::labels_in(text) {
        if !labels.contains(&label) {
            labels.push(label);
        }
    }
    let mentioned = crate::paste_path::image_mentions_in(text);
    let mut unnamed: Vec<&ImageAttachment> = images
        .iter()
        .filter(|image| {
            !labels.contains(&image.alias.as_str()) && !mentioned.contains(&image.alias)
        })
        .collect();
    let mut pending = Vec::new();
    for label in &labels {
        match images.iter().find(|image| image.alias == *label) {
            Some(image) => rebind(app, label, image),
            None => pending.push(*label),
        }
    }
    if pending.len() == unnamed.len() {
        for (label, image) in pending.into_iter().zip(unnamed.drain(..)) {
            rebind(app, label, image);
        }
    }
    crate::image_placeholders::mark_known(&mut app.chat_input);
}

/// Name `image` by `label` again, replacing whatever image the label named.
fn rebind(app: &mut App, label: &str, image: &ImageAttachment) {
    let path = match &image.source {
        ImageSource::File { path } => Some(PathBuf::from(path)),
        ImageSource::Inline { data } => inline_file(data, &image.mime_type),
    };
    if let Some(path) = path {
        app.chat_input
            .pasted_images
            .remember(label, &path.to_string_lossy());
    }
}

fn inline_file(data: &str, mime_type: &str) -> Option<PathBuf> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data)
        .ok()?;
    let extension = match mime_type {
        "image/jpeg" => "jpg",
        "image/gif" => "gif",
        "image/webp" => "webp",
        _ => "png",
    };
    write_private(&bytes, extension).ok()
}

/// Write `bytes` to a new, randomly named temporary file only the user can
/// read: never a path another user could create or link in advance.
pub fn write_private(bytes: &[u8], extension: &str) -> std::io::Result<PathBuf> {
    let mut file = tempfile::Builder::new()
        .prefix(PREFIX)
        .suffix(&format!(".{extension}"))
        .tempfile()?;
    file.write_all(bytes)?;
    let (_file, path) = file.keep().map_err(|error| error.error)?;
    Ok(path)
}
