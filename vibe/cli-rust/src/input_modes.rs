//! Composer mode state and submitted-input classification.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::app::ChatInput;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum InputMode {
    Bash,
    Slash,
    Teleport,
    #[default]
    Prompt,
}

impl InputMode {
    pub fn prefix(self) -> Option<char> {
        match self {
            Self::Bash => Some('!'),
            Self::Slash => Some('/'),
            Self::Teleport => Some('&'),
            Self::Prompt => None,
        }
    }

    /// The mode a leading character opens when it starts the composer text.
    pub fn opened_by(character: char) -> Option<Self> {
        match character {
            '!' => Some(Self::Bash),
            '/' => Some(Self::Slash),
            '&' if crate::commands::has_command("/teleport") => Some(Self::Teleport),
            _ => None,
        }
    }

    /// Whether a pasted image shows as an `[Image #N]` placeholder: only text
    /// that goes through prompt preparation can resolve one. A shell command
    /// gets the raw path.
    pub fn names_images(self) -> bool {
        matches!(self, Self::Prompt | Self::Slash)
    }

    pub fn marker(self) -> &'static str {
        match self {
            Self::Bash => "! ",
            Self::Slash => "/ ",
            Self::Teleport => "& ",
            Self::Prompt => "> ",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ClassifiedInput {
    Teleport { target: String },
    SlashCommand { command: &'static str },
    Skill { command: String, name: String },
    Bash { command: String },
    EmptyBash,
    Prompt { text: String },
}

pub fn classify(value: &str, skills: &[(String, String)]) -> ClassifiedInput {
    if let Some(target) = value
        .strip_prefix('&')
        .filter(|_| crate::commands::has_command("/teleport"))
    {
        return ClassifiedInput::Teleport {
            target: target.trim().to_owned(),
        };
    }
    if let Some(command) = crate::commands::parse(value) {
        return match command {
            "/loop" => ClassifiedInput::Prompt {
                text: value.to_owned(),
            },
            _ => ClassifiedInput::SlashCommand { command },
        };
    }
    if let Some(rest) = value.strip_prefix('/') {
        let name = rest.split_whitespace().next().unwrap_or_default();
        if let Some((skill, _)) = skills
            .iter()
            .find(|(skill, _)| skill.eq_ignore_ascii_case(name))
        {
            return ClassifiedInput::Skill {
                command: value.to_owned(),
                name: skill.clone(),
            };
        }
    }
    if let Some(command) = value.strip_prefix('!') {
        return match command.is_empty() {
            true => ClassifiedInput::EmptyBash,
            false => ClassifiedInput::Bash {
                command: command.to_owned(),
            },
        };
    }
    ClassifiedInput::Prompt {
        text: value.to_owned(),
    }
}

/// Pasted text that only looks prefixed (paths, code, markdown images); a paste never opens teleport.
fn pasted_literally(mode: InputMode, rest: &str) -> bool {
    let word = rest.split_whitespace().next().unwrap_or_default();
    match mode {
        InputMode::Slash => !word
            .chars()
            .all(|c| c.is_alphanumeric() || matches!(c, '-' | '_' | ':' | '.')),
        InputMode::Bash => rest.starts_with('['),
        InputMode::Teleport => true,
        InputMode::Prompt => false,
    }
}

fn literal_prefix(mode: InputMode, value: &str) -> bool {
    mode == InputMode::Prompt && value.trim_start().starts_with(['/', '!', '&'])
}

/// Trimmed submitted text; one leading space stays when it keeps a prompt's mode character literal.
pub fn submitted_value(mode: InputMode, text: &str) -> String {
    let trimmed = text.trim();
    match literal_prefix(mode, text) && text.starts_with(char::is_whitespace) {
        true => format!(" {trimmed}"),
        false => trimmed.to_owned(),
    }
}

/// In prompt mode, a leading mode character (typed after the text or behind whitespace) is message text.
pub fn classify_submitted(
    mode: InputMode,
    value: &str,
    skills: &[(String, String)],
) -> ClassifiedInput {
    if literal_prefix(mode, value) {
        // The server still invokes a leading skill, so it keeps its skill telemetry.
        return match classify(value.trim_start(), skills) {
            ClassifiedInput::Skill { name, .. } => ClassifiedInput::Skill {
                command: value.to_owned(),
                name,
            },
            _ => ClassifiedInput::Prompt {
                text: value.to_owned(),
            },
        };
    }
    classify(value, skills)
}

impl ChatInput {
    /// Whether an insertion replaces all the prompt text, so a mode character opens its mode.
    fn replaces_all(&self) -> bool {
        self.mode == InputMode::Prompt
            && (self.input.is_empty()
                || crate::chat_input::selection_range(&self.input, self.cursor, self.anchor)
                    == Some((0, self.input.len())))
    }

    fn open_mode(&mut self, mode: InputMode) {
        self.input.clear();
        self.mentions.clear();
        self.expandable_paste = None;
        self.cursor = 0;
        self.anchor = None;
        self.mode = mode;
    }

    /// Open a typed mode over empty or fully selected text, or leave it on Backspace at the start.
    pub fn apply_mode_key(&mut self, key: &KeyEvent) -> bool {
        if let KeyCode::Char(character) = key.code {
            let modified = key
                .modifiers
                .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
            let Some(mode) =
                InputMode::opened_by(character).filter(|_| !modified && self.replaces_all())
            else {
                return false;
            };
            self.open_mode(mode);
            return true;
        }
        let at_start = self.cursor == 0
            && crate::chat_input::selection_range(&self.input, self.cursor, self.anchor).is_none();
        if key.code != KeyCode::Backspace || self.mode == InputMode::Prompt || !at_start {
            return false;
        }
        self.mode = InputMode::Prompt;
        true
    }

    /// Open the mode a paste over all the prompt text starts with; returns the text left to insert.
    pub fn open_pasted_mode<'a>(&mut self, text: &'a str) -> &'a str {
        let Some(mode) = text
            .chars()
            .next()
            .and_then(InputMode::opened_by)
            .filter(|mode| self.replaces_all() && !pasted_literally(*mode, &text[1..]))
        else {
            return text;
        };
        self.open_mode(mode);
        &text[1..]
    }

    pub fn full_text(&self) -> String {
        match self.mode.prefix() {
            Some(prefix) => format!("{prefix}{}", self.input),
            None => self.input.clone(),
        }
    }

    /// Load composer text, opening the mode its first character names.
    pub fn load_full_text(&mut self, text: String) {
        let (mode, body) = match text.as_bytes().first() {
            Some(b'!') => (InputMode::Bash, &text[1..]),
            Some(b'/') => (InputMode::Slash, &text[1..]),
            Some(b'&') => (InputMode::Teleport, &text[1..]),
            _ => (InputMode::Prompt, text.as_str()),
        };
        self.load(mode, body.to_owned());
    }

    /// Load a sent or queued message as a prompt: commands never reach the server.
    pub fn load_prompt_text(&mut self, text: String) {
        self.load(InputMode::Prompt, text);
    }

    fn load(&mut self, mode: InputMode, body: String) {
        self.edit_history.clear();
        self.mode = mode;
        self.input = body;
        self.mentions.clear();
        self.expandable_paste = None;
        self.cursor = self.input.len();
        self.anchor = None;
        self.scroll = None;
    }

    pub fn clear(&mut self) {
        self.edit_history.clear();
        self.input.clear();
        self.mentions.clear();
        self.expandable_paste = None;
        self.mode = InputMode::Prompt;
        self.cursor = 0;
        self.anchor = None;
        self.scroll = None;
    }
}
