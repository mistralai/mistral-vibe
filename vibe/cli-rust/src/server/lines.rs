//! Bounded line splitting for the app-server's and the client's streams.

use tokio::io::{AsyncRead, AsyncReadExt};

/// Leading bytes kept from an oversized line, enough to say what it was.
const PREFIX_MAX: usize = 256;

pub enum Line {
    Frame(Vec<u8>),
    /// A line past the reader's cap, dropped but for its first bytes.
    Oversized(Vec<u8>),
}

/// Splits a stream into lines, skipping those past `max_line` bytes.
pub struct LineReader<R> {
    inner: R,
    max_line: usize,
    tail: Vec<u8>,
    searched: usize,
    skipped_prefix: Option<Vec<u8>>,
    chunk: Vec<u8>,
    eof: bool,
}

impl<R: AsyncRead + Unpin> LineReader<R> {
    pub fn new(inner: R, max_line: usize) -> Self {
        Self {
            inner,
            max_line,
            tail: Vec::new(),
            searched: 0,
            skipped_prefix: None,
            chunk: vec![0; 64 * 1024],
            eof: false,
        }
    }

    /// The next line; None at the end of the stream.
    pub async fn next(&mut self) -> Option<Line> {
        loop {
            if let Some(line) = self.split() {
                return Some(line);
            }
            if self.eof {
                return None;
            }
            match self.inner.read(&mut self.chunk).await {
                Ok(0) | Err(_) => {
                    self.eof = true;
                    if self.skipped_prefix.is_none() && !self.tail.is_empty() {
                        return Some(Line::Frame(std::mem::take(&mut self.tail)));
                    }
                }
                Ok(n) => self.tail.extend_from_slice(&self.chunk[..n]),
            }
        }
    }

    fn split(&mut self) -> Option<Line> {
        let Some(pos) = self.tail[self.searched..].iter().position(|&b| b == b'\n') else {
            if self.skipped_prefix.is_none() && self.tail.len() > self.max_line {
                self.skipped_prefix = Some(self.tail[..PREFIX_MAX.min(self.tail.len())].to_vec());
            }
            if self.skipped_prefix.is_some() {
                self.tail.clear();
            }
            self.searched = self.tail.len();
            return None;
        };
        let mut line: Vec<u8> = self.tail.drain(..=self.searched + pos).collect();
        self.searched = 0;
        if let Some(prefix) = self.skipped_prefix.take() {
            return Some(Line::Oversized(prefix));
        }
        line.pop();
        Some(Line::Frame(line))
    }
}
