"""The banner cat plays one full round at startup, then rests until clicked."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance, take_snapshot

# The fixture's handshake seeds the startup config taken from `runtime/read`
# with the banner animation disabled; this scenario watches the cat play, so
# it pins the seed back to the animation being enabled.
handshake = {
    "runtime/read": {"runtime": {"config": {"disableWelcomeBannerAnimation": False}}}
}

# A left click at 0-based column 3, row 2: inside the cat's 3 braille rows next to the info.
_CLICK = "\x1b[<0;4;3M\x1b[<0;4;3m"

# The round is a pure function of the scripted UI time, so the snapshots pin the poses.
keep_braille = True

# The round is 26 frames of 160 ms; these advances cover it exactly at 4200 ms.
# The poses matter: mid-round snapshots differ from the resting start pose.
timeline: Timeline = [
    advance(1000),
    take_snapshot(),  # mid-startup round, an animated pose
    advance(3200),
    take_snapshot(),  # the startup round completed, the cat rests at its start pose
    advance(21000),
    take_snapshot(),  # long after, the cat still rests at the same pose
    _CLICK,
    take_snapshot(),  # a plain click on the cat replays the round
    advance(1000),
    take_snapshot(),  # mid-replay, an animated pose again
    advance(4200),
    take_snapshot(),  # the replay completed, the cat rests
    advance(2000),
    take_snapshot(),  # still resting
]
