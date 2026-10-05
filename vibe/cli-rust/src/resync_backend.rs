//! Ratatui prints adjacent cells without cursor moves, trusting the terminal to advance by `unicode-width`.
//! Terminals may draw multi-codepoint clusters like `ណ្ដ` wider, or emoji-prone symbols like `⚠`
//! and `🇺` at a different width than `unicode-width` reports; we move the cursor after each
//! to stop the row drifting. Letters and borders skip the move to keep output small.

use std::cell::Cell as StdCell;
use std::io::{self, Write};
use std::rc::Rc;

use crossterm::cursor::MoveTo;
use crossterm::queue;
use ratatui::backend::{Backend, ClearType, CrosstermBackend, WindowSize};
use ratatui::buffer::Cell;
use ratatui::layout::{Position, Size};

type NextMove = Rc<StdCell<Option<(u16, u16)>>>;

/// Writes the pending cursor move before the next bytes, i.e. before the next cell's output.
pub struct ResyncWriter<W> {
    inner: W,
    next_move: NextMove,
}

impl<W: Write> Write for ResyncWriter<W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        if let Some((x, y)) = self.next_move.take() {
            queue!(self.inner, MoveTo(x, y))?;
        }
        self.inner.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

/// Terminals may size multi-codepoint clusters like `ណ្ដ` or non-ASCII symbols like `🇺`
/// unlike `unicode-width`; an explicit move stops the drift.
pub struct ResyncBackend<W: Write> {
    inner: CrosstermBackend<ResyncWriter<W>>,
    next_move: NextMove,
}

impl<W: Write> ResyncBackend<W> {
    pub fn new(writer: W) -> Self {
        let next_move = NextMove::default();
        let inner = CrosstermBackend::new(ResyncWriter {
            inner: writer,
            next_move: next_move.clone(),
        });
        Self { inner, next_move }
    }
}

// Clusters like `e` + U+0301, emoji-prone symbol blocks and non-BMP codepoints may be drawn at another width.
fn needs_resync(cell: &Cell) -> bool {
    let symbol = cell.symbol();
    let mut chars = symbol.chars();
    match (chars.next(), chars.next()) {
        // Transcript border pieces, drawn one cell wide: skip the move on every bordered row.
        (Some('⎢' | '⎣'), None) => false,
        // Single codepoint: resync inside emoji-prone symbol blocks and above the BMP.
        (Some(c), None) => matches!(c,
            '\u{2100}'..='\u{24FF}' | '\u{25A0}'..='\u{27FF}' | '\u{2900}'..='\u{2BFF}' | '\u{10000}'..),
        // Multi-codepoint cluster, may be drawn wider than `unicode-width`.
        _ => !symbol.is_ascii(),
    }
}

impl<W: Write> Backend for ResyncBackend<W> {
    fn draw<'a, I>(&mut self, content: I) -> io::Result<()>
    where
        I: Iterator<Item = (u16, u16, &'a Cell)>,
    {
        let next_move = self.next_move.clone();
        // Column right after the last drawn cell, if the terminal may have drawn it at another width.
        let mut cursor_unsure_at = None;
        let force_moves = content.inspect(move |&(x, y, cell)| {
            // Ratatui prints this cell without a move, trusting a cursor that may have drifted.
            if cursor_unsure_at == Some((x, y)) {
                // Ratatui pulls a cell before writing its bytes, so the move lands right before it.
                next_move.set(Some((x, y)));
            }
            cursor_unsure_at = needs_resync(cell).then_some((x + 1, y));
        });
        self.inner.draw(force_moves)
    }

    fn append_lines(&mut self, n: u16) -> io::Result<()> {
        self.inner.append_lines(n)
    }

    fn hide_cursor(&mut self) -> io::Result<()> {
        self.inner.hide_cursor()
    }

    fn show_cursor(&mut self) -> io::Result<()> {
        self.inner.show_cursor()
    }

    fn get_cursor_position(&mut self) -> io::Result<Position> {
        self.inner.get_cursor_position()
    }

    fn set_cursor_position<P: Into<Position>>(&mut self, position: P) -> io::Result<()> {
        self.inner.set_cursor_position(position)
    }

    fn clear(&mut self) -> io::Result<()> {
        self.inner.clear()
    }

    fn clear_region(&mut self, clear_type: ClearType) -> io::Result<()> {
        self.inner.clear_region(clear_type)
    }

    fn size(&self) -> io::Result<Size> {
        self.inner.size()
    }

    fn window_size(&mut self) -> io::Result<WindowSize> {
        self.inner.window_size()
    }

    fn flush(&mut self) -> io::Result<()> {
        Backend::flush(&mut self.inner)
    }
}
