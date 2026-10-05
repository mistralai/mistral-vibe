//! Composer mode state and submitted-input classification.

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

impl ChatInput {
    pub fn full_text(&self) -> String {
        match self.mode.prefix() {
            Some(prefix) => format!("{prefix}{}", self.input),
            None => self.input.clone(),
        }
    }

    pub fn load_full_text(&mut self, text: String) {
        self.edit_history.clear();
        let (mode, body) = match text.as_bytes().first() {
            Some(b'!') => (InputMode::Bash, &text[1..]),
            Some(b'/') => (InputMode::Slash, &text[1..]),
            Some(b'&') => (InputMode::Teleport, &text[1..]),
            _ => (InputMode::Prompt, text.as_str()),
        };
        self.mode = mode;
        self.input = body.to_owned();
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
