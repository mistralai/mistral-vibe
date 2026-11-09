//! Each channel's input, and the channel an oversized line belonged to.

use std::io;
use std::pin::Pin;
use std::task::{ready, Context, Poll};

use tokio::io::{AsyncRead, ReadBuf};
use tokio::sync::mpsc;

use crate::server::frames::MAX_FRAME;

/// Longest multiplexed line kept: a channel's frame plus its envelope.
pub(super) const MAX_LINE: usize = MAX_FRAME + 4096;

/// The channel a frame's leading bytes name: `{"channelId":"<n>"`.
pub(super) fn channel_of(prefix: &[u8]) -> Option<u64> {
    let rest = prefix.strip_prefix(br#"{"channelId":""#)?;
    let digits = rest.iter().take_while(|b| b.is_ascii_digit()).count();
    std::str::from_utf8(&rest[..digits]).ok()?.parse().ok()
}

/// A channel's frames, one line each, read by its `Client`; EOF when it closes.
pub(super) struct ChannelInput {
    lines: mpsc::UnboundedReceiver<Vec<u8>>,
    line: Vec<u8>,
    pos: usize,
}

impl ChannelInput {
    pub(super) fn new(lines: mpsc::UnboundedReceiver<Vec<u8>>) -> Self {
        Self {
            lines,
            line: Vec::new(),
            pos: 0,
        }
    }
}

impl AsyncRead for ChannelInput {
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        out: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        while this.pos == this.line.len() {
            match ready!(this.lines.poll_recv(cx)) {
                Some(line) => {
                    this.line = line;
                    this.pos = 0;
                }
                None => return Poll::Ready(Ok(())),
            }
        }
        let n = out.remaining().min(this.line.len() - this.pos);
        out.put_slice(&this.line[this.pos..this.pos + n]);
        this.pos += n;
        Poll::Ready(Ok(()))
    }
}
