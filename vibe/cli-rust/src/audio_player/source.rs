//! Resampling PCM reader feeding the output device (Python `_playback_generator`).

use super::wav::Pcm;

/// Linear-interpolated read of `pcm` at the device's rate and channel count.
pub struct Source {
    pcm: Pcm,
    step: f64,
    position: f64,
}

impl Source {
    pub fn new(pcm: Pcm, output_rate: u32) -> Self {
        let step = f64::from(pcm.sample_rate) / f64::from(output_rate.max(1));
        Self {
            pcm,
            step,
            position: 0.0,
        }
    }

    /// Fill interleaved `out`, silence-padded past the end; returns whether the clip ended.
    pub fn fill(&mut self, out: &mut [f32], channels: usize) -> bool {
        let source_channels = usize::from(self.pcm.channels);
        let frames = self.pcm.samples.len() / source_channels;
        for frame in out.chunks_mut(channels.max(1)) {
            let index = self.position as usize;
            if index >= frames {
                frame.fill(0.0);
                continue;
            }
            let next = (index + 1).min(frames - 1);
            let t = (self.position - index as f64) as f32;
            for (channel, sample) in frame.iter_mut().enumerate() {
                let channel = channel.min(source_channels - 1);
                let a = self.sample(index, channel);
                let b = self.sample(next, channel);
                *sample = a + (b - a) * t;
            }
            self.position += self.step;
        }
        self.position as usize >= frames
    }

    fn sample(&self, frame: usize, channel: usize) -> f32 {
        let at = frame * usize::from(self.pcm.channels) + channel;
        f32::from(self.pcm.samples[at]) / 32768.0
    }
}
