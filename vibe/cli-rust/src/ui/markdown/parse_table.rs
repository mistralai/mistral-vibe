//! Table assembly from the pulldown-cmark event stream.

use super::{Block, Sc};

/// A table being assembled: headers, rows, the row in progress, and head flag.
#[derive(Default)]
pub(super) struct TableB {
    pub headers: Vec<Vec<Sc>>,
    pub rows: Vec<Vec<Vec<Sc>>>,
    row: Vec<Vec<Sc>>,
    in_head: bool,
}

impl TableB {
    pub fn start_head(&mut self) {
        self.in_head = true;
    }

    pub fn end_head(&mut self) {
        self.in_head = false;
    }

    pub fn end_cell(&mut self, cell: Vec<Sc>) {
        if self.in_head {
            self.headers.push(cell);
        } else {
            self.row.push(cell);
        }
    }

    pub fn end_row(&mut self) {
        self.rows.push(std::mem::take(&mut self.row));
    }

    pub fn finish(self) -> Block {
        Block::Table {
            headers: self.headers,
            rows: self.rows,
        }
    }
}
