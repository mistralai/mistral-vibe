"""Property-based / fuzz coverage for the ReviewManager shell on real files.

Where test_history_properties.py fuzzes the pure projection, this drives the
impure manager (real temp files, the actual approve_review/revert_review API used
by ACP and the e2e suite) across many randomized multi-file, multi-turn sessions
with user edits between turns. It checks the same anchor invariants end to end:

  - revert-all               -> every file back to its pre-agent original
  - approve-all              -> every file byte-identical to the current disk
  - per-turn revert, one at a time (persisting each) -> back to the originals

Randomness is seeded; failures reprint the seed for a deterministic repro.
"""

from __future__ import annotations

from pathlib import Path
import random

import pytest

from vibe.core.checkpoints import (
    Checkpointer,
    CheckpointRecorder,
    FileSnapshot,
    FileState,
)
from vibe.core.review import AllTarget, ReviewManager, ScopeTarget
from vibe.core.types import LLMMessage, MessageList, Role

FILES = ["a.txt", "b.txt", "c.txt"]


def to_text(lines: list[str]) -> str:
    return "".join(f"{line}\n" for line in lines)


def to_lines(text: str) -> list[str]:
    return text.split("\n")[:-1] if text else []


def perturb(lines: list[str], rng: random.Random, tag: str) -> list[str]:
    for _ in range(8):
        out = list(lines)
        if not out:
            out = [tag]
        else:
            op = rng.choice(["rep", "ins", "del", "block", "edge"])
            i = rng.randrange(len(out))
            if op == "rep":
                out[i] = tag
            elif op == "ins":
                out[i:i] = [f"{tag}_{j}" for j in range(rng.randint(1, 3))]
            elif op == "del":
                del out[i : i + rng.randint(1, 2)]
            elif op == "block":
                out[i : i + rng.randint(1, 2)] = [f"{tag}a", f"{tag}b"]
            elif rng.random() < 0.5:
                out.insert(0, f"{tag}top")
            else:
                out.append(f"{tag}end")
        if out != lines:
            return out
    return [*lines, f"{tag}x"]


Turn = tuple[dict[str, str], dict[str, str]]  # (user_edits, agent_edits)


def gen_scenario(
    rng: random.Random, *, with_user_edits: bool = True
) -> tuple[dict[str, str], list[Turn], dict[str, str]]:
    originals = {
        name: to_text([f"{name[0]}{i}" for i in range(rng.randint(1, 4))])
        for name in FILES
    }
    state = dict(originals)
    turns: list[Turn] = []
    for turn in range(rng.randint(2, 5)):
        user_edits: dict[str, str] = {}
        if with_user_edits and turn > 0:
            for name in FILES:
                if rng.random() < 0.25:
                    state[name] = to_text(
                        perturb(to_lines(state[name]), rng, f"U{turn}")
                    )
                    user_edits[name] = state[name]
        # Turn 0 touches every file so all are tracked from the start.
        targets = (
            FILES
            if turn == 0
            else [f for f in FILES if rng.random() < 0.6] or [rng.choice(FILES)]
        )
        agent_edits: dict[str, str] = {}
        for name in targets:
            state[name] = to_text(perturb(to_lines(state[name]), rng, f"T{turn}"))
            agent_edits[name] = state[name]
        turns.append((user_edits, agent_edits))
    return originals, turns, dict(state)


def _snap(path: Path) -> FileSnapshot:
    try:
        content: bytes | None = path.read_bytes()
    except FileNotFoundError:
        content = None
    return FileSnapshot(path=str(path.resolve()), state=FileState(content))


class _Bundle:
    """The capture + review shells over one shared Checkpointer, exposing the
    flat surface this suite drives. Production wires them separately.
    """

    def __init__(self, messages: MessageList) -> None:
        checkpointer = Checkpointer()
        recorder = CheckpointRecorder(checkpointer, messages)
        review = ReviewManager(checkpointer)
        self.create_checkpoint = recorder.create_checkpoint
        self.add_snapshot = recorder.add_snapshot
        self.seal_turn = recorder.seal_turn
        self.review_state = review.review_state
        self.approve_review = review.approve_review
        self.revert_review = review.revert_review


def _fresh_manager() -> tuple[_Bundle, MessageList]:
    messages = MessageList([LLMMessage(role=Role.system, content="system")])
    return _Bundle(messages), messages


def _replay(
    tmp: Path, originals: dict[str, str], turns: list[Turn]
) -> tuple[_Bundle, MessageList]:
    """Recreate the session on disk: seed originals, then run each turn as the
    manager would (grow messages so turn ids differ, capture pre via snapshots /
    carry-forward, write the edit, seal).
    """
    for name, text in originals.items():
        (tmp / name).write_text(text, encoding="utf-8")

    mgr, messages = _fresh_manager()
    for turn, (user_edits, agent_edits) in enumerate(turns):
        # A turn appends a user+assistant pair; the manager keys turns by length.
        messages.append(LLMMessage(role=Role.user, content=f"p{turn}"))
        messages.append(LLMMessage(role=Role.assistant, content=f"r{turn}"))
        # Between-turn user edits land before the checkpoint's carry-forward read.
        for name, text in user_edits.items():
            (tmp / name).write_text(text, encoding="utf-8")
        mgr.create_checkpoint()
        if turn == 0:
            for name in FILES:
                mgr.add_snapshot(_snap(tmp / name))
        for name, text in agent_edits.items():
            (tmp / name).write_text(text, encoding="utf-8")
        mgr.seal_turn()
    return mgr, messages


def _disk(tmp: Path) -> dict[str, str]:
    return {name: (tmp / name).read_text(encoding="utf-8") for name in FILES}


SEEDS = range(80)


@pytest.mark.parametrize("seed", SEEDS)
def test_revert_all_restores_every_original(seed: int, tmp_path: Path) -> None:
    rng = random.Random(seed)
    originals, turns, _final = gen_scenario(rng)
    mgr, _ = _replay(tmp_path, originals, turns)

    mgr.revert_review(AllTarget())

    assert _disk(tmp_path) == originals, f"seed={seed}: revert-all != originals"


@pytest.mark.parametrize("seed", SEEDS)
def test_approve_all_leaves_disk_unchanged(seed: int, tmp_path: Path) -> None:
    rng = random.Random(seed)
    originals, turns, final = gen_scenario(rng)
    mgr, _ = _replay(tmp_path, originals, turns)

    before = _disk(tmp_path)
    assert before == final, f"seed={seed}: replay disk != generated final state"

    mgr.approve_review(AllTarget())

    assert _disk(tmp_path) == final, f"seed={seed}: approve-all changed disk"


@pytest.mark.parametrize("seed", SEEDS)
def test_incremental_per_turn_revert_restores_originals(
    seed: int, tmp_path: Path
) -> None:
    rng = random.Random(seed)
    # Agent-only turns: reverting every agent turn should reach the pristine
    # original. (With between-turn user edits those local layers correctly
    # survive per-turn reverts, so that case is covered by revert-all instead.)
    originals, turns, _final = gen_scenario(rng, with_user_edits=False)
    mgr, _ = _replay(tmp_path, originals, turns)

    # Revert each turn on its own, persisting between — the manager's real flow
    # and the exact shape that surfaced the deletion-duplication bug.
    turn_ids = [rt.owner for rt in mgr.review_state().scopes]
    for turn_id in turn_ids:
        mgr.revert_review(ScopeTarget(owner=turn_id))

    assert _disk(tmp_path) == originals, (
        f"seed={seed}: per-turn revert != originals (turn_ids={turn_ids})"
    )
