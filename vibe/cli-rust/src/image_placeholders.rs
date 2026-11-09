//! `[Image #N]` placeholders standing for pasted images in the composer.

use std::collections::VecDeque;

use crate::server::{ImageAttachment, ImageSource, PreparedPrompt};

/// Bound on remembered pasted images; the oldest placeholder stops resolving first.
pub const MAX_PASTED_IMAGES: usize = 256;

/// Pasted images by placeholder, numbered for the whole process, and above
/// every placeholder the transcript already shows, so a label never names two
/// images in one conversation, a resumed one included.
#[derive(Default)]
pub struct PastedImages {
    last: usize,
    entries: VecDeque<(String, String)>,
}

impl PastedImages {
    /// Remember the image at `path` and return its new placeholder, numbered
    /// past `used`, the highest number the conversation already shows.
    pub fn register(&mut self, path: &str, used: usize) -> String {
        self.last = self.last.max(used).saturating_add(1);
        let label = format!("[Image #{}]", self.last);
        self.entries.push_back((label.clone(), path.to_owned()));
        if self.entries.len() > MAX_PASTED_IMAGES {
            self.entries.pop_front();
        }
        label
    }

    /// Whether `label` names a remembered image.
    pub fn knows(&self, label: &str) -> bool {
        self.entries.iter().any(|(known, _)| known == label)
    }

    /// Remember the image at `path` under an existing `label`, such as the
    /// placeholder of a rewound prompt; later pastes number past it.
    pub fn remember(&mut self, label: &str, path: &str) {
        self.last = self.last.max(highest_number(label));
        self.entries.retain(|(known, _)| known != label);
        self.entries.push_back((label.to_owned(), path.to_owned()));
        if self.entries.len() > MAX_PASTED_IMAGES {
            self.entries.pop_front();
        }
    }

    /// `text` with each known placeholder swapped for its image's `@path`
    /// mention: the form a placeholder keeps outside this process.
    pub fn with_paths(&self, text: &str) -> String {
        self.entries
            .iter()
            .filter(|(label, _)| text.contains(label.as_str()))
            .fold(text.to_owned(), |text, (label, path)| {
                text.replace(label.as_str(), &crate::paste_path::image_path_mention(path))
            })
    }

    /// `images` plus an attachment for every known placeholder `text` holds.
    pub fn attach(&self, text: &str, mut images: Vec<ImageAttachment>) -> Vec<ImageAttachment> {
        for (label, path) in &self.entries {
            if text.contains(label.as_str()) && !images.iter().any(|image| image.alias == *label) {
                images.push(ImageAttachment {
                    source: ImageSource::File { path: path.clone() },
                    alias: label.clone(),
                    mime_type: mime_type(path).to_owned(),
                });
            }
        }
        images
    }
}

pub fn is_placeholder(alias: &str) -> bool {
    alias.starts_with("[Image #") && alias.ends_with(']')
}

/// The `[Image #N]` placeholders of `text`, in order.
pub fn labels_in(text: &str) -> Vec<&str> {
    let mut labels = Vec::new();
    let mut rest = text;
    let mut offset = 0;
    while let Some(found) = rest.find("[Image #") {
        let start = offset + found;
        let digits = text[start + 8..]
            .bytes()
            .take_while(u8::is_ascii_digit)
            .count();
        let end = start + 8 + digits;
        if digits > 0 && text[end..].starts_with(']') {
            labels.push(&text[start..=end]);
        }
        offset = start + 1;
        rest = &text[offset..];
    }
    labels
}

/// The highest `N` among the `[Image #N]` placeholders of `text`, or 0.
pub fn highest_number(text: &str) -> usize {
    labels_in(text)
        .into_iter()
        .filter_map(|label| label[8..label.len() - 1].parse().ok())
        .max()
        .unwrap_or(0)
}

/// Mark every `[Image #N]` of the input that names a remembered image as a
/// mention, as loading text into the composer forgets them.
pub fn mark_known(input: &mut crate::app::ChatInput) {
    let mut starts = Vec::new();
    for label in labels_in(&input.input) {
        if input.pasted_images.knows(label) {
            let start = label.as_ptr() as usize - input.input.as_ptr() as usize;
            starts.push((start, start + label.len()));
        }
    }
    for (start, end) in starts {
        input.add_mention(start, end);
    }
}

/// Whether `text` still references the placeholder `image` is named after.
pub fn references(text: &str, image: &ImageAttachment) -> bool {
    is_placeholder(&image.alias) && text.contains(image.alias.as_str())
}

/// Swap each file-backed placeholder for its `@path` mention, so prompt
/// preparation snapshots the image like any other image mention. A
/// placeholder touching anything but whitespace gets a space on that side, so
/// the mention stays its own token whatever characters a path may hold.
pub fn expand(text: &str, images: &[ImageAttachment]) -> String {
    placeholder_paths(images).fold(text.to_owned(), |text, (label, path)| {
        let mention = crate::paste_path::image_path_mention(path);
        let mut out = String::with_capacity(text.len());
        let mut at = 0;
        for (start, _) in text.match_indices(label) {
            let end = start + label.len();
            out.push_str(&text[at..start]);
            if text[..start]
                .chars()
                .next_back()
                .is_some_and(|c| !c.is_whitespace())
            {
                out.push(' ');
            }
            out.push_str(&mention);
            if text[end..]
                .chars()
                .next()
                .is_some_and(|c| !c.is_whitespace())
            {
                out.push(' ');
            }
            at = end;
        }
        out.push_str(&text[at..]);
        out
    })
}

/// Undo `expand` on a prepared prompt: the model reads the placeholders, and
/// each snapshot of a placeholder's image carries that placeholder as its name.
/// Placeholders sharing one path (the same file pasted twice) were prepared
/// once, so the later ones share that snapshot rather than the unsent file.
pub fn collapse(prepared: &mut PreparedPrompt, text: &str, images: &[ImageAttachment]) {
    let mut snapshots: Vec<(&str, ImageAttachment)> = Vec::new();
    for (label, path) in placeholder_paths(images) {
        if let Some(image) = prepared.images.iter_mut().find(|image| image.alias == path) {
            image.alias = label.to_owned();
            snapshots.push((path, image.clone()));
        } else if let Some((_, snapshot)) = snapshots.iter().find(|(seen, _)| *seen == path) {
            let mut shared = snapshot.clone();
            shared.alias = label.to_owned();
            prepared.images.push(shared);
        }
    }
    prepared.display_text = text.to_owned();
    prepared.prompt_text = Some(text.to_owned());
}

fn placeholder_paths(images: &[ImageAttachment]) -> impl Iterator<Item = (&str, &str)> {
    images.iter().filter_map(|image| match &image.source {
        ImageSource::File { path } if is_placeholder(&image.alias) => {
            Some((image.alias.as_str(), path.as_str()))
        }
        _ => None,
    })
}

fn mime_type(path: &str) -> &'static str {
    let extension = std::path::Path::new(path)
        .extension()
        .and_then(|extension| extension.to_str())
        .map(str::to_ascii_lowercase);
    match extension.as_deref() {
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("webp") => "image/webp",
        _ => "image/png",
    }
}
