//! Microphone capture via cpal (Python `AudioRecorder`, STREAM mode).

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Arc;
use std::time::Duration;

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{FromSample, Sample, SampleFormat, SizedSample};
use tokio::sync::mpsc::{self, UnboundedReceiver, UnboundedSender};

/// Open the default input device and stream mono `pcm_s16le` chunks until `stop`.
/// Returns the chunk receiver and the sample rate actually captured at.
pub fn start(
    _requested_rate: u32,
    peak: Arc<AtomicU32>,
    stop: Arc<AtomicBool>,
) -> Result<(UnboundedReceiver<Vec<u8>>, u32), String> {
    let host = cpal::default_host();
    let device = host
        .default_input_device()
        .ok_or_else(|| "No audio input device found.".to_string())?;
    let supported = device
        .default_input_config()
        .map_err(|e| format!("Audio backend is unavailable: {e}"))?;
    let sample_rate = supported.sample_rate().0;
    let channels = supported.channels() as usize;
    let sample_format = supported.sample_format();
    let config: cpal::StreamConfig = supported.into();

    let (tx, rx) = mpsc::unbounded_channel::<Vec<u8>>();
    let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<(), String>>();

    // cpal streams are not Send on macOS; build and own it on a dedicated thread.
    std::thread::spawn(move || {
        let stream = match build(&device, &config, channels, sample_format, tx, peak) {
            Ok(s) => s,
            Err(e) => {
                let _ = ready_tx.send(Err(e));
                return;
            }
        };
        if let Err(e) = stream.play() {
            let _ = ready_tx.send(Err(format!("Audio backend is unavailable: {e}")));
            return;
        }
        let _ = ready_tx.send(Ok(()));
        while !stop.load(Ordering::Relaxed) {
            std::thread::sleep(Duration::from_millis(20));
        }
        // Dropping the stream stops capture and drops the sender, closing `rx`.
        drop(stream);
    });

    match ready_rx.recv() {
        Ok(Ok(())) => Ok((rx, sample_rate)),
        Ok(Err(e)) => Err(e),
        Err(_) => Err("Audio thread exited before start".to_string()),
    }
}

/// Dispatch on the device's sample format; each branch downmixes to mono i16.
fn build(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    channels: usize,
    fmt: SampleFormat,
    tx: UnboundedSender<Vec<u8>>,
    peak: Arc<AtomicU32>,
) -> Result<cpal::Stream, String> {
    match fmt {
        SampleFormat::F32 => build_typed::<f32>(device, config, channels, tx, peak),
        SampleFormat::I16 => build_typed::<i16>(device, config, channels, tx, peak),
        SampleFormat::U16 => build_typed::<u16>(device, config, channels, tx, peak),
        other => Err(format!("Unsupported sample format {other:?}")),
    }
}

fn build_typed<T>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    channels: usize,
    tx: UnboundedSender<Vec<u8>>,
    peak: Arc<AtomicU32>,
) -> Result<cpal::Stream, String>
where
    T: SizedSample + Send + 'static,
    i16: FromSample<T>,
{
    let err_fn = |e| tracing::warn!(error = %e, "audio stream error");
    device
        .build_input_stream(
            config,
            move |data: &[T], _: &cpal::InputCallbackInfo| {
                let mut bytes = Vec::with_capacity(data.len() / channels.max(1) * 2);
                let mut block_peak = 0.0_f32;
                for frame in data.chunks(channels.max(1)) {
                    let mut acc = 0_i32;
                    for &sample in frame {
                        acc += i16::from_sample(sample) as i32;
                    }
                    let mono = (acc / channels.max(1) as i32) as i16;
                    let amp = (mono as f32 / 32768.0).abs();
                    if amp > block_peak {
                        block_peak = amp;
                    }
                    bytes.extend_from_slice(&mono.to_le_bytes());
                }
                peak.store(block_peak.to_bits(), Ordering::Relaxed);
                let _ = tx.send(bytes);
            },
            err_fn,
            None,
        )
        .map_err(|e| format!("Audio backend is unavailable: {e}"))
}
