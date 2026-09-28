//! Image attachments retained by queued prompt edits.

use super::QueueItem;
use crate::server::{ImageAttachment, ImageSource, PreparedPrompt};

impl QueueItem {
    /// Restore file-backed attachments as editable composer mentions.
    pub fn edit_text(&self) -> String {
        let text = crate::paste_path::rewrite_bare_image_paths_in_text(&self.text);
        let mentions: Vec<String> = self
            .images
            .iter()
            .filter_map(|image| match &image.source {
                ImageSource::File { path } if !image_is_mentioned(&text, image) => {
                    Some(crate::paste_path::image_path_mention(path))
                }
                _ => None,
            })
            .collect();
        match (mentions.is_empty(), text.is_empty()) {
            (true, _) => text,
            (false, true) => mentions.join(" "),
            (false, false) => format!("{} {text}", mentions.join(" ")),
        }
    }
}

fn image_is_mentioned(text: &str, image: &ImageAttachment) -> bool {
    let ImageSource::File { path } = &image.source else {
        return false;
    };
    crate::paste_path::contains_image_path_mention(text, path)
        || crate::paste_path::contains_image_path_mention(text, &image.alias)
}

/// Preserve attachments represented by the edited composer text or only as inline data.
pub fn merge_edit_images(prepared: &mut PreparedPrompt, existing: &[ImageAttachment], text: &str) {
    for image in existing {
        let retained = match &image.source {
            ImageSource::File { .. } => image_is_mentioned(text, image),
            ImageSource::Inline { .. } => true,
        };
        if !retained {
            continue;
        }
        let already_prepared = prepared.images.iter().any(|candidate| match &image.source {
            ImageSource::File { path } => {
                candidate.source == image.source
                    || candidate.alias == path.as_str()
                    || candidate.alias == image.alias
            }
            ImageSource::Inline { .. } => candidate.source == image.source,
        });
        if !already_prepared {
            prepared.images.push(image.clone());
        }
    }
}
