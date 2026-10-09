//! Speaker playback via cpal (Python `AudioPlayer`).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{FromSample, SampleFormat, SizedSample};
use tokio::sync::oneshot;

use super::source::Source;
use super::wav::Pcm;
use super::AudioError;

/// Python `DEFAULT_BUFFER_MS`: the device drains its buffer before the stream closes.
const TAIL: Duration = Duration::from_millis(200);
const POLL: Duration = Duration::from_millis(20);
/// Slack past the clip's length before a stalled device counts as done.
const STALL_MARGIN: Duration = Duration::from_secs(2);
/// Callback buffer reserved up front so the realtime thread does not allocate.
const BUFFER_SAMPLES: usize = 16_384;

type Done = oneshot::Receiver<Result<(), AudioError>>;

/// Python `check_audio_available`; blocking, so run it off the async runtime.
pub fn check_available() -> Result<(), AudioError> {
    cpal::default_host()
        .default_output_device()
        .map(|_| ())
        .ok_or_else(AudioError::no_device)
}

/// Start `pcm` on the default output device; `Done` resolves when it ends or `stop` is set.
pub async fn play(pcm: Pcm, stop: Arc<AtomicBool>) -> Result<Done, AudioError> {
    let (ready_tx, ready_rx) = oneshot::channel();
    let (done_tx, done_rx) = oneshot::channel();
    // cpal streams are not Send on macOS, and device lookup blocks: both live on this thread.
    std::thread::spawn(move || {
        let length = Duration::from_secs_f64(
            pcm.samples.len() as f64 / f64::from(pcm.channels) / f64::from(pcm.sample_rate),
        );
        let ended = Arc::new(AtomicBool::new(false));
        let failure = Arc::new(Mutex::new(None));
        let stream = match open(pcm, ended.clone(), failure.clone()) {
            Ok(stream) => stream,
            Err(error) => {
                let _ = ready_tx.send(Err(error));
                return;
            }
        };
        let _ = ready_tx.send(Ok(()));
        let deadline = Instant::now() + length + STALL_MARGIN;
        let failed = || failure.lock().is_ok_and(|failure| failure.is_some());
        while !stop.load(Ordering::Relaxed)
            && !ended.load(Ordering::Relaxed)
            && !failed()
            && Instant::now() < deadline
        {
            std::thread::sleep(POLL);
        }
        if ended.load(Ordering::Relaxed) && !stop.load(Ordering::Relaxed) {
            std::thread::sleep(TAIL);
        }
        drop(stream);
        let error = failure.lock().ok().and_then(|mut failure| failure.take());
        let _ = done_tx.send(error.map_or(Ok(()), Err));
    });
    match ready_rx.await {
        Ok(Ok(())) => Ok(done_rx),
        Ok(Err(error)) => Err(error),
        Err(_) => Err(AudioError::backend(
            "Audio thread exited before start".into(),
        )),
    }
}

fn open(
    pcm: Pcm,
    ended: Arc<AtomicBool>,
    failure: Arc<Mutex<Option<AudioError>>>,
) -> Result<cpal::Stream, AudioError> {
    let device = cpal::default_host()
        .default_output_device()
        .ok_or_else(AudioError::no_device)?;
    let supported = device
        .default_output_config()
        .map_err(|e| AudioError::backend(e.to_string()))?;
    let format = supported.sample_format();
    let config: cpal::StreamConfig = supported.into();
    let source = Source::new(pcm, config.sample_rate.0);
    let stream = match format {
        SampleFormat::F32 => build::<f32>(&device, &config, source, ended, failure),
        SampleFormat::F64 => build::<f64>(&device, &config, source, ended, failure),
        SampleFormat::I16 => build::<i16>(&device, &config, source, ended, failure),
        SampleFormat::I32 => build::<i32>(&device, &config, source, ended, failure),
        SampleFormat::U16 => build::<u16>(&device, &config, source, ended, failure),
        SampleFormat::U8 => build::<u8>(&device, &config, source, ended, failure),
        other => Err(AudioError::backend(format!(
            "Unsupported sample format {other:?}"
        ))),
    }?;
    stream
        .play()
        .map_err(|e| AudioError::backend(e.to_string()))?;
    Ok(stream)
}

fn build<T>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    mut source: Source,
    ended: Arc<AtomicBool>,
    failure: Arc<Mutex<Option<AudioError>>>,
) -> Result<cpal::Stream, AudioError>
where
    T: SizedSample + FromSample<f32> + Send + 'static,
{
    let channels = usize::from(config.channels);
    let mut buffer = Vec::with_capacity(BUFFER_SAMPLES);
    device
        .build_output_stream(
            config,
            move |data: &mut [T], _: &cpal::OutputCallbackInfo| {
                buffer.resize(data.len(), 0.0);
                if source.fill(&mut buffer, channels) {
                    ended.store(true, Ordering::Relaxed);
                }
                for (out, sample) in data.iter_mut().zip(&buffer) {
                    *out = T::from_sample(*sample);
                }
            },
            move |e| {
                tracing::warn!(error = %e, "audio output stream error");
                if let Ok(mut failure) = failure.lock() {
                    failure.get_or_insert_with(|| AudioError::backend(e.to_string()));
                }
            },
            None,
        )
        .map_err(|e| AudioError::backend(e.to_string()))
}
