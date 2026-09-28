# 0016 Narration Playback in the ACP Subprocess

## Context

Narration (text-to-speech of the agent's turn summary) was originally played in
the webview via Chrome's `AudioContext`. The ACP subprocess synthesized the
audio and returned base64-encoded WAV over the Comlink bridge; the webview
decoded it and played it locally.

This created three problems:

1. **Audio bytes crossed the postMessage bridge.** A full base64 WAV blob
   per narration inflated 33% over the wire and required `atob` + `decodeAudioData`
   in the webview.
2. **The webview fought the platform.** VS Code webviews block `AudioContext`
   outside user gestures, requiring a first-interaction unlock dance, CSP
   `media-src` allowances, and Blob-URL workarounds — all friction that exists
   only because audio played in Chrome.
3. **No device-following.** Chrome's audio engine re-resolves the system
   default on each playback, but moving playback to PortAudio introduced a new
   constraint: PortAudio's CoreAudio backend caches the default output device
   at init and never re-reads the OS. Naive playback would be stuck on the
   startup-time device forever.

## Decision

Narration plays in the ACP subprocess through PortAudio (`sounddevice`), the
same backend used for dictation's microphone capture. The host and webview
receive only lifecycle notifications — `narrationPrep`, `narrationDone`,
`narrationError` — never the audio bytes. This mirrors the dictation
architecture: generation and I/O live where the API key and audio hardware
live; the UI reflects state from pushed events.

### Device following

Because PortAudio caches the default output at `Pa_Initialize` and never
refreshes it from the OS, a `Pa_Terminate` + `Pa_Initialize` cycle is required
to re-scan. The reinit invalidates any open PortAudio stream, so it must not
run while the microphone (transcription) is active. Two layers handle this:

- **Per-clip reinit** (`AudioPlayer.set_before_play`): before each narration,
  the owner (which tracks whether the mic is active) reinits PortAudio to
  refresh the default. Safe because narration fires after a turn completes,
  when the mic is guaranteed closed.
- **Mid-stream following** (`DeviceFollower`): during playback, a poller thread
  queries CoreAudio's `kAudioHardwarePropertyDefaultOutputDevice` directly via
  raw `ctypes` (no pyobjc dependency — its pythonic wrapper fights the
  pointer-heavy `AudioObjectGetPropertyData` signature). On change, the player
  stops the stream, reinits, resolves the new default, and reopens — resuming
  from the same PCM position. The gap is tens of milliseconds.

`DeviceFollower` is macOS-only; on other platforms it is inert and the
per-clip reinit still handles between-narration switches.

### NarrationController

`VoiceController` delegates narration to `NarrationController`, a separate
class that owns the audio player, the generation counter (supersession), and
a serialization lock. The controller receives an `is_input_active` callback
at construction so it can guard the reinit without coupling to
transcription's internals.

## Consequences

- No audio bytes cross the Comlink bridge. The webview's `AudioContext`,
  CSP media-src, and user-gesture unlock code are deleted entirely.
- Narration follows the system default output device, even mid-playback on
  macOS. On other platforms, switches take effect on the next narration.
- Narration is local-only: the audio plays on the machine where the ACP
  subprocess runs. Remote / devcontainer / SSH scenarios emit sound on the
  remote host, not the user's headphones. This is symmetric with dictation,
  which already requires a local microphone.
- `pyobjc` is not a dependency; the CoreAudio bridge uses `ctypes` directly.
- Mid-stream following adds a poller thread per playback. The gap on switch
  is audible but continuous (no content dropped or repeated).
