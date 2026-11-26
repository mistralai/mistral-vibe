//! Narration audio: WAV decoding and the resampling source fed to the device.

use vibe_rs::audio_player::source::Source;
use vibe_rs::audio_player::wav::{decode_wav, Pcm};

fn wav(format: u16, channels: u16, rate: u32, bits: u16, samples: &[i16]) -> Vec<u8> {
    let data: Vec<u8> = samples.iter().flat_map(|s| s.to_le_bytes()).collect();
    let mut out = b"RIFF\0\0\0\0WAVE".to_vec();
    out.extend_from_slice(b"LIST\x03\0\0\0abc\0");
    out.extend_from_slice(b"fmt \x10\0\0\0");
    out.extend_from_slice(&format.to_le_bytes());
    out.extend_from_slice(&channels.to_le_bytes());
    out.extend_from_slice(&rate.to_le_bytes());
    out.extend_from_slice(&(rate * u32::from(channels) * 2).to_le_bytes());
    out.extend_from_slice(&(channels * 2).to_le_bytes());
    out.extend_from_slice(&bits.to_le_bytes());
    out.extend_from_slice(b"data");
    out.extend_from_slice(&(data.len() as u32).to_le_bytes());
    out.extend_from_slice(&data);
    out
}

fn pcm(sample_rate: u32, channels: u16, samples: &[i16]) -> Pcm {
    Pcm {
        sample_rate,
        channels,
        samples: samples.to_vec(),
    }
}

#[test]
fn decodes_pcm_past_padded_unknown_chunks() {
    let bytes = wav(1, 2, 24_000, 16, &[1, -2, 3, -4]);
    assert_eq!(decode_wav(&bytes), Ok(pcm(24_000, 2, &[1, -2, 3, -4])));
}

#[test]
fn a_streamed_data_size_keeps_the_bytes_that_arrived() {
    let mut bytes = wav(1, 1, 16_000, 16, &[7, 8]);
    let size_at = bytes.len() - 8;
    bytes[size_at..size_at + 4].copy_from_slice(&u32::MAX.to_le_bytes());
    assert_eq!(decode_wav(&bytes).unwrap().samples, [7, 8]);
}

#[test]
fn rejects_what_the_player_cannot_play() {
    assert!(decode_wav(b"ID3\x04 not a wav").is_err());
    assert!(decode_wav(&wav(1, 1, 16_000, 8, &[0])).is_err());
    assert!(decode_wav(&wav(3, 1, 16_000, 16, &[0])).is_err());
    assert!(decode_wav(b"RIFF\0\0\0\0WAVE").is_err());
}

#[test]
fn same_rate_mono_duplicates_to_every_device_channel() {
    let mut source = Source::new(pcm(48_000, 1, &[16_384, -16_384]), 48_000);
    let mut out = [9.0; 4];
    assert!(source.fill(&mut out, 2));
    assert_eq!(out, [0.5, 0.5, -0.5, -0.5]);
}

#[test]
fn upsampling_interpolates_then_pads_with_silence() {
    let mut source = Source::new(pcm(24_000, 1, &[0, 16_384]), 48_000);
    let mut out = [9.0; 3];
    assert!(!source.fill(&mut out, 1));
    assert_eq!(out, [0.0, 0.25, 0.5]);
    let mut tail = [9.0; 3];
    assert!(source.fill(&mut tail, 1));
    assert_eq!(tail, [0.5, 0.0, 0.0]);
}

#[test]
fn an_empty_clip_is_silence_and_already_finished() {
    let mut source = Source::new(pcm(24_000, 1, &[]), 48_000);
    let mut out = [9.0; 2];
    assert!(source.fill(&mut out, 2));
    assert_eq!(out, [0.0, 0.0]);
}
