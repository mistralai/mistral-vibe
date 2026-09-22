//! Config option row construction.

use super::config::{Opt, ADVANCED, CURSOR_W, GAP, NAME_W, POPULAR, VALUE_W};
use crate::config::ConfigField;

pub(super) fn build_lines(fields: &[ConfigField], selected: usize, sections: bool) -> Vec<Opt> {
    let mut out: Vec<Opt> = Vec::new();
    if !sections {
        return fields
            .iter()
            .enumerate()
            .map(|(rank, field)| Opt::Row {
                selected: rank == selected,
                name: truncate(&field.name, NAME_W),
                value: truncate(&field.value, VALUE_W),
            })
            .collect();
    }
    let popular: Vec<&ConfigField> = fields.iter().filter(|f| f.popular).collect();
    let advanced: Vec<&ConfigField> = fields.iter().filter(|f| !f.popular).collect();
    let mut rank = 0usize;
    let mut first = true;
    for (label, section) in [(POPULAR, popular), (ADVANCED, advanced)] {
        if section.is_empty() {
            continue;
        }
        if !first {
            out.push(Opt::Blank);
            out.push(Opt::Blank);
        }
        out.push(Opt::Section(label.to_owned()));
        out.push(Opt::Blank);
        out.push(Opt::Header(columns_header()));
        for field in section {
            out.push(Opt::Row {
                selected: rank == selected,
                name: truncate(&field.name, NAME_W),
                value: truncate(&field.value, VALUE_W),
            });
            rank += 1;
        }
        first = false;
    }
    out
}

fn columns_header() -> String {
    format!(
        "{}{:<name$}{}VALUE",
        " ".repeat(CURSOR_W),
        "SETTING",
        " ".repeat(GAP),
        name = NAME_W
    )
}

fn truncate(value: &str, width: usize) -> String {
    let count = value.chars().count();
    if count <= width {
        return value.to_owned();
    }
    if width <= 1 {
        return value.chars().take(width).collect();
    }
    let head: String = value.chars().take(width - 1).collect();
    format!("{head}…")
}
