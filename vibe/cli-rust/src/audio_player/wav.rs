//! 16-bit PCM WAV decoding (Python `decode_wav`).

const FORMAT_PCM: u16 = 1;
const FORMAT_EXTENSIBLE: u16 = 0xFFFE;

/// Interleaved 16-bit samples and their layout.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Pcm {
    pub sample_rate: u32,
    pub channels: u16,
    pub samples: Vec<i16>,
}

/// Walk the RIFF chunks to `fmt ` and `data`; a truncated `data` chunk keeps what arrived.
pub fn decode_wav(bytes: &[u8]) -> Result<Pcm, String> {
    if bytes.len() < 12 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return Err("Audio is not a WAV file".to_owned());
    }
    let mut fmt = None;
    let mut rest = &bytes[12..];
    while rest.len() >= 8 {
        let size = u32::from_le_bytes([rest[4], rest[5], rest[6], rest[7]]) as usize;
        let body = &rest[8..rest.len().min(8usize.saturating_add(size))];
        match &rest[0..4] {
            b"fmt " if body.len() >= 16 => {
                fmt = Some((
                    u16_at(body, 0),
                    u16_at(body, 2),
                    u32::from_le_bytes([body[4], body[5], body[6], body[7]]),
                    u16_at(body, 14),
                ));
            }
            b"data" => return pcm(fmt, body),
            _ => {}
        }
        let advance = 8usize.saturating_add(size).saturating_add(size & 1);
        rest = rest.get(advance..).unwrap_or_default();
    }
    Err("WAV audio has no data chunk".to_owned())
}

fn pcm(fmt: Option<(u16, u16, u32, u16)>, data: &[u8]) -> Result<Pcm, String> {
    let Some((format, channels, sample_rate, bits)) = fmt else {
        return Err("WAV audio has no fmt chunk".to_owned());
    };
    let pcm_format = format == FORMAT_PCM || format == FORMAT_EXTENSIBLE;
    if !pcm_format || bits != 16 || channels == 0 || sample_rate == 0 {
        return Err(format!(
            "Unsupported WAV encoding: format {format}, {bits}-bit, {channels} channels"
        ));
    }
    let samples = data
        .as_chunks::<2>()
        .0
        .iter()
        .map(|b| i16::from_le_bytes(*b))
        .collect();
    Ok(Pcm {
        sample_rate,
        channels,
        samples,
    })
}

fn u16_at(body: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([body[at], body[at + 1]])
}
