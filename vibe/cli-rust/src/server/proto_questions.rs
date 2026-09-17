//! `ask_user_question` wire types, mirroring Python's `vibe/questions.py`.

use serde::{Deserialize, Serialize};

/// One selectable answer (`QuestionChoice`).
#[derive(Debug, Clone, Deserialize)]
pub struct QuestionChoice {
    pub label: String,
    #[serde(default)]
    pub description: String,
}

/// One question of a request (`UserQuestion`).
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UserQuestion {
    pub question: String,
    #[serde(default)]
    pub header: String,
    pub options: Vec<QuestionChoice>,
    #[serde(default)]
    pub multi_select: bool,
    #[serde(default)]
    pub hide_other: bool,
}

/// The `user_input` callback detail payload (`UserQuestionRequest`).
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UserQuestionRequest {
    pub questions: Vec<UserQuestion>,
    #[serde(default)]
    pub footer_note: Option<String>,
}

/// One answered question (`UserAnswer`).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UserAnswer {
    pub question: String,
    pub answer: String,
    pub is_other: bool,
}

/// The `callback/result` output payload (`UserQuestionResult`).
#[derive(Debug, Clone, Serialize)]
pub struct UserQuestionResult {
    pub answers: Vec<UserAnswer>,
    pub cancelled: bool,
}
